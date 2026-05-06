/**
 * Market worker - single-writer per market
 * Processes commands for a specific market with deterministic ordering
 */

import type { PoolClient, Pool } from "pg";
import type { Logger } from "pino";
import { MatchingEngine } from "../engine/matching.js";
import { isValidStep } from "../engine/deterministic.js";
import type {
  Order,
  MarketConfig,
  PlaceLimitOrderCommand,
  PlaceMarketOrderCommand,
  CancelOrderCommand,
  ReplaceOrderCommand,
  OrderResult
} from "../engine/types.js";
import {
  MarketRepo,
  OrdersRepo,
  TradesRepo,
  EventsRepo,
  OutboxRepo,
  SequenceRepo,
  IdempotencyRepo
} from "../db/repositories/index.js";
import { writeOrderEvent, writeTradeEvent } from "../outbox/outbox.js";

// Constants for market order price limits
const MAX_PRICE_ATOMS = BigInt("999999999999999999"); // High price for market buy orders
const MIN_PRICE_ATOMS = 1n; // Low price for market sell orders

export class MarketWorker {
  private engine: MatchingEngine | null = null;
  private marketConfig: MarketConfig | null = null;

  constructor(
    private marketId: string,
    private pool: Pool,
    private logger: Logger
  ) {}

  /**
   * Initialize worker - load market config and rebuild orderbook
   */
  async initialize(): Promise<void> {
    const client = await this.pool.connect();
    try {
      const marketRepo = new MarketRepo(client);
      const ordersRepo = new OrdersRepo(client);
      const sequenceRepo = new SequenceRepo(client);

      // Load market config
      this.marketConfig = await marketRepo.getById(this.marketId);
      if (!this.marketConfig) {
        throw new Error(`Market not found: ${this.marketId}`);
      }

      if (!this.marketConfig.active) {
        throw new Error(`Market not active: ${this.marketId}`);
      }

      // Get current sequence
      const currentSeq = await sequenceRepo.getCurrentSequence(this.marketId);

      // Create engine
      this.engine = new MatchingEngine(this.marketConfig, currentSeq);

      // Rebuild orderbook from open orders
      const openOrders = await ordersRepo.getOpenOrdersByMarket(this.marketId);
      this.engine.rebuildFromOrders(openOrders);

      this.logger.info(
        {
          marketId: this.marketId,
          symbol: this.marketConfig.symbol,
          openOrders: openOrders.length,
          sequence: currentSeq.toString()
        },
        "Market worker initialized"
      );
    } finally {
      client.release();
    }
  }

  /**
   * Place a limit order
   */
  async placeLimitOrder(cmd: PlaceLimitOrderCommand): Promise<OrderResult> {
    if (!this.engine || !this.marketConfig) {
      throw new Error("Worker not initialized");
    }

    const client = await this.pool.connect();
    try {
      await client.query("BEGIN");

      const idempotencyRepo = new IdempotencyRepo(client);
      const cached = await idempotencyRepo.get(cmd.idempotencyKey, "matching-engine");
      if (cached) {
        await client.query("ROLLBACK");
        return cached as OrderResult;
      }

      const ordersRepo = new OrdersRepo(client);
      const tradesRepo = new TradesRepo(client);
      const eventsRepo = new EventsRepo(client);
      const outboxRepo = new OutboxRepo(client);
      const sequenceRepo = new SequenceRepo(client);

      // Validate tick/step
      if (!isValidStep(cmd.priceAtoms, this.marketConfig.priceTick)) {
        const result: OrderResult = {
          success: false,
          fills: [],
          trades: [],
          events: [],
          rejectReason: "Invalid price tick"
        };
        await idempotencyRepo.set(cmd.idempotencyKey, "matching-engine", result);
        await client.query("COMMIT");
        return result;
      }

      if (!isValidStep(cmd.sizeAtoms, this.marketConfig.sizeStep)) {
        const result: OrderResult = {
          success: false,
          fills: [],
          trades: [],
          events: [],
          rejectReason: "Invalid size step"
        };
        await idempotencyRepo.set(cmd.idempotencyKey, "matching-engine", result);
        await client.query("COMMIT");
        return result;
      }

      if (cmd.sizeAtoms < this.marketConfig.minOrderSize) {
        const result: OrderResult = {
          success: false,
          fills: [],
          trades: [],
          events: [],
          rejectReason: "Below minimum order size"
        };
        await idempotencyRepo.set(cmd.idempotencyKey, "matching-engine", result);
        await client.query("COMMIT");
        return result;
      }

      // Create order
      const acceptedAt = new Date();
      const order = await ordersRepo.createOrder({
        userId: cmd.userId,
        clientOrderId: cmd.clientOrderId,
        marketId: cmd.marketId,
        side: cmd.side,
        orderType: "LIMIT",
        timeInForce: cmd.timeInForce,
        priceAtoms: cmd.priceAtoms,
        sizeAtoms: cmd.sizeAtoms,
        postOnly: cmd.postOnly,
        acceptedAt
      });

      // Check post-only
      if (cmd.postOnly) {
        if (this.engine.getOrderBook().wouldCross(cmd.side, cmd.priceAtoms)) {
          await ordersRepo.rejectOrder(order.id, "Post-only order would cross");
          order.status = "REJECTED";
          const result: OrderResult = {
            success: false,
            order,
            fills: [],
            trades: [],
            events: [],
            rejectReason: "Post-only order would cross"
          };
          await idempotencyRepo.set(cmd.idempotencyKey, "matching-engine", result);
          await client.query("COMMIT");
          return result;
        }
      }

      // Write ACCEPTED event
      const acceptSeq = await sequenceRepo.nextSequence(this.marketId);
      await eventsRepo.appendEvent({
        orderId: order.id,
        marketId: this.marketId,
        eventType: "ACCEPTED",
        sequence: acceptSeq,
        payload: { order }
      });
      await writeOrderEvent(outboxRepo, this.marketId, acceptSeq, "ACCEPTED", order);

      // Match order
      let matchResult;
      try {
        matchResult = this.engine.match(order);
      } catch (error) {
        // Matching error (e.g., FOK cannot fill)
        await ordersRepo.rejectOrder(order.id, (error as Error).message);
        order.status = "REJECTED";
        const rejectSeq = await sequenceRepo.nextSequence(this.marketId);
        await eventsRepo.appendEvent({
          orderId: order.id,
          marketId: this.marketId,
          eventType: "REJECTED",
          sequence: rejectSeq,
          payload: { order, reason: (error as Error).message }
        });
        await writeOrderEvent(outboxRepo, this.marketId, rejectSeq, "REJECTED", order);
        const result: OrderResult = {
          success: false,
          order,
          fills: [],
          trades: [],
          events: [],
          rejectReason: (error as Error).message
        };
        await idempotencyRepo.set(cmd.idempotencyKey, "matching-engine", result);
        await client.query("COMMIT");
        return result;
      }

      if (
        matchResult.takerOrder.timeInForce === "IOC" &&
        matchResult.takerOrder.remainingAtoms > 0n
      ) {
        matchResult.takerOrder.status = "EXPIRED";
      }

      // Write trades
      await tradesRepo.insertTrades(matchResult.trades);

      // Write trade events to outbox
      for (const trade of matchResult.trades) {
        await writeTradeEvent(outboxRepo, trade);
      }

      // Update maker orders
      for (const [orderId, makerOrder] of matchResult.makerUpdates) {
        await ordersRepo.updateOrderFill(
          orderId,
          makerOrder.filledAtoms,
          makerOrder.remainingAtoms,
          makerOrder.status
        );

        // Write maker event
        const makerSeq = await sequenceRepo.nextSequence(this.marketId);
        const eventType = makerOrder.status === "FILLED" ? "FILLED" : "PARTIAL_FILL";
        await eventsRepo.appendEvent({
          orderId: makerOrder.id,
          marketId: this.marketId,
          eventType,
          sequence: makerSeq,
          payload: { order: makerOrder, fills: matchResult.fills }
        });
        await writeOrderEvent(outboxRepo, this.marketId, makerSeq, eventType, makerOrder);
      }

      // Update taker order
      await ordersRepo.updateOrderFill(
        matchResult.takerOrder.id,
        matchResult.takerOrder.filledAtoms,
        matchResult.takerOrder.remainingAtoms,
        matchResult.takerOrder.status
      );

      // Write taker event
      if (
        matchResult.takerOrder.filledAtoms > 0n ||
        matchResult.takerOrder.status === "EXPIRED"
      ) {
        const takerSeq = await sequenceRepo.nextSequence(this.marketId);
        const eventType =
          matchResult.takerOrder.status === "FILLED"
            ? "FILLED"
            : matchResult.takerOrder.status === "EXPIRED"
              ? "EXPIRED"
              : "PARTIAL_FILL";
        await eventsRepo.appendEvent({
          orderId: matchResult.takerOrder.id,
          marketId: this.marketId,
          eventType,
          sequence: takerSeq,
          payload: { order: matchResult.takerOrder, fills: matchResult.fills }
        });
        await writeOrderEvent(
          outboxRepo,
          this.marketId,
          takerSeq,
          eventType,
          matchResult.takerOrder
        );
      }

      // If order has remaining and should rest on book
      if (
        matchResult.takerOrder.remainingAtoms > 0n &&
        matchResult.takerOrder.timeInForce !== "IOC"
      ) {
        this.engine.addOrder(matchResult.takerOrder);
      }

      const result: OrderResult = {
        success: true,
        order: matchResult.takerOrder,
        fills: matchResult.fills,
        trades: matchResult.trades,
        events: []
      };

      // Cache result
      await idempotencyRepo.set(cmd.idempotencyKey, "matching-engine", result);

      await client.query("COMMIT");

      this.logger.info(
        {
          orderId: order.id,
          fills: matchResult.fills.length,
          trades: matchResult.trades.length
        },
        "Limit order processed"
      );

      return result;
    } catch (error) {
      await client.query("ROLLBACK");
      throw error;
    } finally {
      client.release();
    }
  }

  /**
   * Place a market order
   */
  async placeMarketOrder(cmd: PlaceMarketOrderCommand): Promise<OrderResult> {
    if (!this.engine || !this.marketConfig) {
      throw new Error("Worker not initialized");
    }

    const client = await this.pool.connect();
    try {
      await client.query("BEGIN");

      const idempotencyRepo = new IdempotencyRepo(client);
      const cached = await idempotencyRepo.get(cmd.idempotencyKey, "matching-engine");
      if (cached) {
        await client.query("ROLLBACK");
        return cached as OrderResult;
      }

      const ordersRepo = new OrdersRepo(client);
      const tradesRepo = new TradesRepo(client);
      const eventsRepo = new EventsRepo(client);
      const outboxRepo = new OutboxRepo(client);
      const sequenceRepo = new SequenceRepo(client);

      // Validate size
      if (!isValidStep(cmd.sizeAtoms, this.marketConfig.sizeStep)) {
        const result: OrderResult = {
          success: false,
          fills: [],
          trades: [],
          events: [],
          rejectReason: "Invalid size step"
        };
        await idempotencyRepo.set(cmd.idempotencyKey, "matching-engine", result);
        await client.query("COMMIT");
        return result;
      }

      if (cmd.sizeAtoms < this.marketConfig.minOrderSize) {
        const result: OrderResult = {
          success: false,
          fills: [],
          trades: [],
          events: [],
          rejectReason: "Below minimum order size"
        };
        await idempotencyRepo.set(cmd.idempotencyKey, "matching-engine", result);
        await client.query("COMMIT");
        return result;
      }

      // Create market order (price = 0 for market orders)
      const acceptedAt = new Date();
      const order = await ordersRepo.createOrder({
        userId: cmd.userId,
        clientOrderId: cmd.clientOrderId,
        marketId: cmd.marketId,
        side: cmd.side,
        orderType: "MARKET",
        timeInForce: "IOC", // Market orders are always IOC
        priceAtoms: 0n,
        sizeAtoms: cmd.sizeAtoms,
        postOnly: false,
        acceptedAt
      });

      // Set price to infinity for matching
      if (cmd.side === "BUY") {
        order.priceAtoms = MAX_PRICE_ATOMS; // High price for buying
      } else {
        order.priceAtoms = MIN_PRICE_ATOMS; // Low price for selling
      }

      // Write ACCEPTED event
      const acceptSeq = await sequenceRepo.nextSequence(this.marketId);
      await eventsRepo.appendEvent({
        orderId: order.id,
        marketId: this.marketId,
        eventType: "ACCEPTED",
        sequence: acceptSeq,
        payload: { order }
      });
      await writeOrderEvent(outboxRepo, this.marketId, acceptSeq, "ACCEPTED", order);

      // Match order
      const matchResult = this.engine.match(order);

      if (matchResult.takerOrder.remainingAtoms > 0n) {
        matchResult.takerOrder.status = "EXPIRED";
      }

      // Market orders should not rest on book
      if (matchResult.takerOrder.remainingAtoms > 0n) {
        this.logger.warn(
          {
            orderId: order.id,
            remaining: matchResult.takerOrder.remainingAtoms.toString()
          },
          "Market order partially filled"
        );
      }

      // Write trades
      await tradesRepo.insertTrades(matchResult.trades);

      // Write trade events
      for (const trade of matchResult.trades) {
        await writeTradeEvent(outboxRepo, trade);
      }

      // Update maker orders
      for (const [orderId, makerOrder] of matchResult.makerUpdates) {
        await ordersRepo.updateOrderFill(
          orderId,
          makerOrder.filledAtoms,
          makerOrder.remainingAtoms,
          makerOrder.status
        );

        const makerSeq = await sequenceRepo.nextSequence(this.marketId);
        const eventType = makerOrder.status === "FILLED" ? "FILLED" : "PARTIAL_FILL";
        await eventsRepo.appendEvent({
          orderId: makerOrder.id,
          marketId: this.marketId,
          eventType,
          sequence: makerSeq,
          payload: { order: makerOrder, fills: matchResult.fills }
        });
        await writeOrderEvent(outboxRepo, this.marketId, makerSeq, eventType, makerOrder);
      }

      // Update taker order
      await ordersRepo.updateOrderFill(
        matchResult.takerOrder.id,
        matchResult.takerOrder.filledAtoms,
        matchResult.takerOrder.remainingAtoms,
        matchResult.takerOrder.status
      );

      if (
        matchResult.takerOrder.filledAtoms > 0n ||
        matchResult.takerOrder.status === "EXPIRED"
      ) {
        const takerSeq = await sequenceRepo.nextSequence(this.marketId);
        const eventType =
          matchResult.takerOrder.status === "FILLED"
            ? "FILLED"
            : matchResult.takerOrder.status === "EXPIRED"
              ? "EXPIRED"
              : "PARTIAL_FILL";
        await eventsRepo.appendEvent({
          orderId: matchResult.takerOrder.id,
          marketId: this.marketId,
          eventType,
          sequence: takerSeq,
          payload: { order: matchResult.takerOrder, fills: matchResult.fills }
        });
        await writeOrderEvent(
          outboxRepo,
          this.marketId,
          takerSeq,
          eventType,
          matchResult.takerOrder
        );
      }

      const result: OrderResult = {
        success: true,
        order: matchResult.takerOrder,
        fills: matchResult.fills,
        trades: matchResult.trades,
        events: []
      };

      await idempotencyRepo.set(cmd.idempotencyKey, "matching-engine", result);
      await client.query("COMMIT");

      this.logger.info(
        {
          orderId: order.id,
          fills: matchResult.fills.length,
          trades: matchResult.trades.length
        },
        "Market order processed"
      );

      return result;
    } catch (error) {
      await client.query("ROLLBACK");
      throw error;
    } finally {
      client.release();
    }
  }

  /**
   * Cancel an order
   */
  async cancelOrder(cmd: CancelOrderCommand): Promise<OrderResult> {
    if (!this.engine || !this.marketConfig) {
      throw new Error("Worker not initialized");
    }

    const client = await this.pool.connect();
    try {
      await client.query("BEGIN");

      const idempotencyRepo = new IdempotencyRepo(client);
      const cached = await idempotencyRepo.get(cmd.idempotencyKey, "matching-engine");
      if (cached) {
        await client.query("ROLLBACK");
        return cached as OrderResult;
      }

      const ordersRepo = new OrdersRepo(client);
      const eventsRepo = new EventsRepo(client);
      const outboxRepo = new OutboxRepo(client);
      const sequenceRepo = new SequenceRepo(client);

      // Get order
      const order = await ordersRepo.getById(cmd.orderId);
      if (!order) {
        const result: OrderResult = {
          success: false,
          fills: [],
          trades: [],
          events: [],
          rejectReason: "Order not found"
        };
        await idempotencyRepo.set(cmd.idempotencyKey, "matching-engine", result);
        await client.query("COMMIT");
        return result;
      }

      // Check ownership
      if (order.userId !== cmd.userId) {
        const result: OrderResult = {
          success: false,
          fills: [],
          trades: [],
          events: [],
          rejectReason: "Order not owned by user"
        };
        await idempotencyRepo.set(cmd.idempotencyKey, "matching-engine", result);
        await client.query("COMMIT");
        return result;
      }

      // Check if cancelable
      if (order.status !== "ACCEPTED" && order.status !== "PARTIAL_FILL") {
        const result: OrderResult = {
          success: false,
          order,
          fills: [],
          trades: [],
          events: [],
          rejectReason: `Cannot cancel order in status ${order.status}`
        };
        await idempotencyRepo.set(cmd.idempotencyKey, "matching-engine", result);
        await client.query("COMMIT");
        return result;
      }

      // Remove from book
      this.engine.removeOrder(order.id);

      // Cancel in DB
      await ordersRepo.cancelOrder(order.id);
      order.status = "CANCELED";

      // Write event
      const seq = await sequenceRepo.nextSequence(this.marketId);
      await eventsRepo.appendEvent({
        orderId: order.id,
        marketId: this.marketId,
        eventType: "CANCELED",
        sequence: seq,
        payload: { order }
      });
      await writeOrderEvent(outboxRepo, this.marketId, seq, "CANCELED", order);

      const result: OrderResult = {
        success: true,
        order,
        fills: [],
        trades: [],
        events: []
      };

      await idempotencyRepo.set(cmd.idempotencyKey, "matching-engine", result);
      await client.query("COMMIT");

      this.logger.info({ orderId: order.id }, "Order canceled");

      return result;
    } catch (error) {
      await client.query("ROLLBACK");
      throw error;
    } finally {
      client.release();
    }
  }

  /**
   * Replace an order (cancel and create new)
   */
  async replaceOrder(cmd: ReplaceOrderCommand): Promise<OrderResult> {
    if (!this.engine || !this.marketConfig) {
      throw new Error("Worker not initialized");
    }

    const client = await this.pool.connect();
    try {
      await client.query("BEGIN");

      const idempotencyRepo = new IdempotencyRepo(client);
      const cached = await idempotencyRepo.get(cmd.idempotencyKey, "matching-engine");
      if (cached) {
        await client.query("ROLLBACK");
        return cached as OrderResult;
      }

      const ordersRepo = new OrdersRepo(client);
      const eventsRepo = new EventsRepo(client);
      const outboxRepo = new OutboxRepo(client);
      const sequenceRepo = new SequenceRepo(client);

      // Get existing order
      const existingOrder = await ordersRepo.getById(cmd.orderId);
      if (!existingOrder) {
        const result: OrderResult = {
          success: false,
          fills: [],
          trades: [],
          events: [],
          rejectReason: "Order not found"
        };
        await idempotencyRepo.set(cmd.idempotencyKey, "matching-engine", result);
        await client.query("COMMIT");
        return result;
      }

      // Check ownership
      if (existingOrder.userId !== cmd.userId) {
        const result: OrderResult = {
          success: false,
          fills: [],
          trades: [],
          events: [],
          rejectReason: "Order not owned by user"
        };
        await idempotencyRepo.set(cmd.idempotencyKey, "matching-engine", result);
        await client.query("COMMIT");
        return result;
      }

      // Can only replace open/partial orders
      if (existingOrder.status !== "ACCEPTED" && existingOrder.status !== "PARTIAL_FILL") {
        const result: OrderResult = {
          success: false,
          order: existingOrder,
          fills: [],
          trades: [],
          events: [],
          rejectReason: `Cannot replace order in status ${existingOrder.status}`
        };
        await idempotencyRepo.set(cmd.idempotencyKey, "matching-engine", result);
        await client.query("COMMIT");
        return result;
      }

      // Remove from book
      this.engine.removeOrder(existingOrder.id);

      // Mark as replaced
      await ordersRepo.markReplaced(existingOrder.id);
      existingOrder.status = "CANCELED_REPLACED";

      // Write canceled event
      const cancelSeq = await sequenceRepo.nextSequence(this.marketId);
      await eventsRepo.appendEvent({
        orderId: existingOrder.id,
        marketId: this.marketId,
        eventType: "CANCELED_REPLACED",
        sequence: cancelSeq,
        payload: { order: existingOrder }
      });
      await writeOrderEvent(
        outboxRepo,
        this.marketId,
        cancelSeq,
        "CANCELED_REPLACED",
        existingOrder
      );

      // Create new order with updated params
      const newPriceAtoms = cmd.newPriceAtoms ?? existingOrder.priceAtoms;
      const newSizeAtoms = cmd.newSizeAtoms ?? existingOrder.remainingAtoms;
      const timeInForce = cmd.timeInForce ?? existingOrder.timeInForce;
      const postOnly = cmd.postOnly ?? existingOrder.postOnly;

      // Validate
      if (!isValidStep(newPriceAtoms, this.marketConfig.priceTick)) {
        const result: OrderResult = {
          success: false,
          order: existingOrder,
          fills: [],
          trades: [],
          events: [],
          rejectReason: "Invalid price tick"
        };
        await idempotencyRepo.set(cmd.idempotencyKey, "matching-engine", result);
        await client.query("COMMIT");
        return result;
      }

      if (!isValidStep(newSizeAtoms, this.marketConfig.sizeStep)) {
        const result: OrderResult = {
          success: false,
          order: existingOrder,
          fills: [],
          trades: [],
          events: [],
          rejectReason: "Invalid size step"
        };
        await idempotencyRepo.set(cmd.idempotencyKey, "matching-engine", result);
        await client.query("COMMIT");
        return result;
      }

      // Create new order (keep same client_order_id)
      const acceptedAt = new Date();
      const newOrder = await ordersRepo.createOrder({
        userId: existingOrder.userId,
        clientOrderId: existingOrder.clientOrderId,
        marketId: existingOrder.marketId,
        side: existingOrder.side,
        orderType: existingOrder.orderType,
        timeInForce,
        priceAtoms: newPriceAtoms,
        sizeAtoms: newSizeAtoms,
        postOnly,
        acceptedAt,
        replaceOf: existingOrder.id
      });

      // Write accepted event
      const acceptSeq = await sequenceRepo.nextSequence(this.marketId);
      await eventsRepo.appendEvent({
        orderId: newOrder.id,
        marketId: this.marketId,
        eventType: "ACCEPTED",
        sequence: acceptSeq,
        payload: { order: newOrder, replacedFrom: existingOrder.id }
      });
      await writeOrderEvent(outboxRepo, this.marketId, acceptSeq, "ACCEPTED", newOrder);

      // Add to book (don't match on replace)
      this.engine.addOrder(newOrder);

      const result: OrderResult = {
        success: true,
        order: newOrder,
        fills: [],
        trades: [],
        events: []
      };

      await idempotencyRepo.set(cmd.idempotencyKey, "matching-engine", result);
      await client.query("COMMIT");

      this.logger.info(
        {
          oldOrderId: existingOrder.id,
          newOrderId: newOrder.id
        },
        "Order replaced"
      );

      return result;
    } catch (error) {
      await client.query("ROLLBACK");
      throw error;
    } finally {
      client.release();
    }
  }
}
