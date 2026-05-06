/**
 * NATS Consumer for Ledger Service
 * 
 * Consumes trade and order events from the matching engine's outbox
 * Ensures exactly-once processing with idempotency checks
 */

import type { NatsConnection, Msg } from "nats";
import type { Pool } from "pg";
import type { Logger } from "pino";
import { jsonCodec } from "@cex/common";
import { withSerializableTransaction } from "../db/tx.js";
import {
  AccountsRepo,
  LedgerRepo,
  IdempotencyRepo,
  BalancesRepo
} from "../db/repositories/index.js";
import { handleTradeEvent } from "./handlers/trade_settle.js";
import { handleOrderLock } from "./handlers/order_lock.js";
import { handleOrderRelease } from "./handlers/order_release.js";
import type { TradeEvent, OrderEvent, Market } from "../domain/types.js";

export class LedgerConsumer {
  private subscriptions: Array<{ unsubscribe: () => void }> = [];

  constructor(
    private pool: Pool,
    private nats: NatsConnection,
    private logger: Logger
  ) {}

  /**
   * Start consuming events for a specific market
   */
  async startMarket(market: Market): Promise<void> {
    this.logger.info({ marketId: market.id, symbol: market.symbol }, "Starting market consumer");

    // Subscribe to trade events for this market
    const tradeSub = this.nats.subscribe(`cex.trade.event.${market.id}`, {
      queue: "ledger-service"
    });
    this.subscriptions.push(tradeSub);

    // Subscribe to order events for this market
    const orderSub = this.nats.subscribe(`cex.order.event.${market.id}`, {
      queue: "ledger-service"
    });
    this.subscriptions.push(orderSub);

    // Process trade events
    (async () => {
      for await (const msg of tradeSub) {
        await this.processTradeMessage(msg, market);
      }
    })().catch((error) => {
      this.logger.error({ error, marketId: market.id }, "Trade subscription error");
    });

    // Process order events
    (async () => {
      for await (const msg of orderSub) {
        await this.processOrderMessage(msg, market);
      }
    })().catch((error) => {
      this.logger.error({ error, marketId: market.id }, "Order subscription error");
    });
  }

  /**
   * Process a trade event message
   */
  private async processTradeMessage(msg: Msg, market: Market): Promise<void> {
    try {
      const decoded = jsonCodec.decode(msg.data);
      const tradeEvent = decoded as TradeEvent;

      this.logger.debug(
        { tradeId: tradeEvent.tradeId, seq: tradeEvent.sequence },
        "Processing trade event"
      );

      const result = await withSerializableTransaction(this.pool, async (client) => {
        const idempotencyRepo = new IdempotencyRepo(client);

        // Check if already processed
        const eventKey = `trade:${tradeEvent.tradeId}`;
        const alreadyProcessed = await idempotencyRepo.checkProcessed(eventKey);
        if (alreadyProcessed) {
          this.logger.info({ tradeId: tradeEvent.tradeId }, "Trade already processed, skipping");
          return { ok: true, alreadyProcessed: true };
        }

        // Check sequence is valid (monotonic)
        const offset = await idempotencyRepo.getOffset(market.id);
        if (!offset) {
          this.logger.error({ marketId: market.id }, "No offset found for market");
          return { ok: false, error: "no_offset_found" };
        }
        const seq = BigInt(tradeEvent.sequence);

        if (seq <= offset.lastTradeSeq) {
          this.logger.warn(
            { seq: seq.toString(), lastSeq: offset.lastTradeSeq.toString() },
            "Trade sequence not monotonic, skipping"
          );
          return { ok: false, error: "sequence_not_monotonic" };
        }

        // Allow gaps temporarily (can implement backfill later)
        // For strict mode: seq === offset.lastTradeSeq + 1n

        // Process the trade
        const result = await handleTradeEvent(client, tradeEvent, market);
        if (!result.ok) {
          this.logger.error({ error: result.error, tradeId: tradeEvent.tradeId }, "Trade processing failed");
          return result;
        }

        // Mark as processed and update offset
        await idempotencyRepo.markProcessed(eventKey, { tradeId: tradeEvent.tradeId });
        await idempotencyRepo.updateOffset(market.id, seq, undefined);

        this.logger.info({ tradeId: tradeEvent.tradeId, seq: seq.toString() }, "Trade settled successfully");
        return { ok: true };
      });

      if (result.ok || (result as any).alreadyProcessed) {
        // ACK not needed for core NATS, only for JetStream
        // msg.ack();
      } else {
        // For errors, we could:
        // 1. NACK and retry (msg.nak()) - only for JetStream
        // 2. ACK and log to dead letter queue
        // 3. Exponential backoff
        // For now, just log the error
        this.logger.error({ error: (result as any).error }, "Trade processing failed");
      }
    } catch (error) {
      this.logger.error({ error }, "Error processing trade message");
      // Don't nak on exception for core NATS
      // msg.nak();
    }
  }

  /**
   * Process an order event message
   */
  private async processOrderMessage(msg: Msg, market: Market): Promise<void> {
    try {
      const decoded = jsonCodec.decode(msg.data);
      const orderEvent = decoded as OrderEvent;

      this.logger.debug(
        { orderId: orderEvent.orderId, eventType: orderEvent.eventType, seq: orderEvent.sequence },
        "Processing order event"
      );

      const result = await withSerializableTransaction(this.pool, async (client) => {
        const idempotencyRepo = new IdempotencyRepo(client);

        // Check if already processed
        const eventKey = `order:${orderEvent.orderId}:${orderEvent.eventType}:${orderEvent.sequence}`;
        const alreadyProcessed = await idempotencyRepo.checkProcessed(eventKey);
        if (alreadyProcessed) {
          this.logger.info({ orderId: orderEvent.orderId, eventType: orderEvent.eventType }, "Order event already processed");
          return { ok: true, alreadyProcessed: true };
        }

        // Check sequence
        const offset = await idempotencyRepo.getOffset(market.id);
        if (!offset) {
          this.logger.error({ marketId: market.id }, "No offset found for market");
          return { ok: false, error: "no_offset_found" };
        }
        const seq = BigInt(orderEvent.sequence);

        if (seq <= offset.lastOrderSeq) {
          this.logger.warn(
            { seq: seq.toString(), lastSeq: offset.lastOrderSeq.toString() },
            "Order sequence not monotonic"
          );
          return { ok: false, error: "sequence_not_monotonic" };
        }

        // Route to appropriate handler
        let result: { ok: boolean; error?: string };
        
        if (orderEvent.eventType === "ACCEPTED") {
          result = await handleOrderLock(client, orderEvent, market);
        } else if (["FILLED", "CANCELED", "CANCELED_REPLACED", "EXPIRED", "REJECTED"].includes(orderEvent.eventType)) {
          result = await handleOrderRelease(client, orderEvent, market);
        } else {
          // PARTIAL_FILL and other non-terminal order events do not change locked balances
          result = { ok: true };
        }

        if (!result.ok) {
          this.logger.error({ error: result.error, orderId: orderEvent.orderId }, "Order processing failed");
          return result;
        }

        // Mark as processed and update offset
        await idempotencyRepo.markProcessed(eventKey, { orderId: orderEvent.orderId, eventType: orderEvent.eventType });
        await idempotencyRepo.updateOffset(market.id, undefined, seq);

        this.logger.info(
          { orderId: orderEvent.orderId, eventType: orderEvent.eventType, seq: seq.toString() },
          "Order event processed"
        );
        return { ok: true };
      });

      if (result.ok || (result as any).alreadyProcessed) {
        // ACK not needed for core NATS
        // msg.ack();
      } else {
        this.logger.error({ error: (result as any).error }, "Order processing failed");
      }
    } catch (error) {
      this.logger.error({ error }, "Error processing order message");
      // msg.nak();
    }
  }

  /**
   * Start consuming deposit credit commands
   */
  async startDepositCredits(): Promise<void> {
    this.logger.info("Starting deposit credit consumer");

    const depositSub = this.nats.subscribe("ledger.deposit.credit", {
      queue: "ledger-service"
    });
    this.subscriptions.push(depositSub);

    // Process deposit credit commands
    (async () => {
      for await (const msg of depositSub) {
        await this.processDepositCreditMessage(msg);
      }
    })().catch((error) => {
      this.logger.error({ error }, "Deposit credit subscription error");
    });
  }

  /**
   * Process a deposit credit message
   */
  private async processDepositCreditMessage(msg: Msg): Promise<void> {
    try {
      const decoded = jsonCodec.decode(msg.data);
      const command = decoded as any; // DepositCreditCommand

      this.logger.info(
        { depositId: command.depositId, userId: command.userId, assetId: command.assetId },
        "Processing deposit credit command"
      );

      const { handleDepositCredit } = await import("./handlers/deposit_credit.js");

      const result = await withSerializableTransaction(this.pool, async (client) => {
        await handleDepositCredit(command, client, this.logger);
        return { ok: true };
      });

      if (result.ok) {
        this.logger.info(
          { depositId: command.depositId, idempotencyKey: command.idempotencyKey },
          "Deposit credit processed successfully"
        );
      }
    } catch (error) {
      this.logger.error({ error, msg: msg.subject }, "Failed to process deposit credit message");
    }
  }

  /**
   * Stop all subscriptions
   */
  async stop(): Promise<void> {
    this.logger.info("Stopping ledger consumer");
    for (const sub of this.subscriptions) {
      sub.unsubscribe();
    }
    this.subscriptions = [];
  }
}
