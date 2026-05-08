/**
 * Market worker - single-writer per market
 * Processes commands for a specific market with deterministic ordering
 */

import type { PoolClient, Pool } from "pg";
import type { Logger } from "pino";
import { MatchingEngine } from "../engine/matching.js";
import { atomsToDecimal, isValidStep } from "../engine/deterministic.js";
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
  SequenceRepo,
  IdempotencyRepo
} from "../db/repositories/index.js";

// Constants for market order price limits
const MAX_PRICE_ATOMS = BigInt("999999999999999999"); // High price for market buy orders
const MIN_PRICE_ATOMS = 1n; // Low price for market sell orders
const PRICE_DECIMALS = 8;
const PRICE_SCALE = 10n ** BigInt(PRICE_DECIMALS);

const FALLBACK_ASSET_DECIMALS: Record<string, number> = {
  ANM: 9,
  BTC: 8,
  BNB: 18,
  LTC: 8,
  DOGE: 8,
  ZEC: 8,
  DASH: 8,
  BCH: 8,
  USDT: 18,
  USDC: 6,
  ETH: 18,
  SOL: 9
};

type BalanceSnapshot = {
  availableAtoms: bigint;
  lockedAtoms: bigint;
};

type TradeOrderPair = {
  makerOrder: Order;
  takerOrder: Order;
};

export class MarketWorker {
  private engine: MatchingEngine | null = null;
  private marketConfig: MarketConfig | null = null;
  private assetDecimals = new Map<string, number>();

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
      const sequenceRepo = new SequenceRepo(client);

      // Load market config
      this.marketConfig = await marketRepo.getById(this.marketId);
      if (!this.marketConfig) {
        throw new Error(`Market not found: ${this.marketId}`);
      }

      if (!this.marketConfig.active) {
        throw new Error(`Market not active: ${this.marketId}`);
      }

      await this.loadAssetDecimals(client);
      const ordersRepo = new OrdersRepo(client, this.getBaseDecimals());

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

  private async loadAssetDecimals(client: PoolClient): Promise<void> {
    if (!this.marketConfig) return;

    const assets = Array.from(
      new Set([
        this.marketConfig.baseAsset,
        this.marketConfig.quoteAsset,
        this.marketConfig.feeAsset
      ].map((asset) => asset.toUpperCase()))
    );

    for (const asset of assets) {
      this.assetDecimals.set(asset, FALLBACK_ASSET_DECIMALS[asset] ?? PRICE_DECIMALS);
    }

    try {
      const result = await client.query(
        `SELECT UPPER(symbol) AS symbol, decimals
           FROM assets
          WHERE UPPER(symbol) = ANY($1::text[])`,
        [assets]
      );

      for (const row of result.rows) {
        const decimals = Number(row.decimals);
        if (Number.isInteger(decimals) && decimals >= 0) {
          this.assetDecimals.set(String(row.symbol).toUpperCase(), decimals);
        }
      }
    } catch (error) {
      this.logger.warn(
        { error, marketId: this.marketId, assets },
        "Falling back to built-in asset decimals"
      );
    }
  }

  private getAssetDecimals(asset: string): number {
    const normalized = asset.toUpperCase();
    return this.assetDecimals.get(normalized) ?? FALLBACK_ASSET_DECIMALS[normalized] ?? PRICE_DECIMALS;
  }

  public getBaseDecimals(): number {
    if (!this.marketConfig) return PRICE_DECIMALS;
    return this.getAssetDecimals(this.marketConfig.baseAsset);
  }

  private assetScale(asset: string): bigint {
    return 10n ** BigInt(this.getAssetDecimals(asset));
  }

  private accountIdForUser(userId: string): string {
    return `user:${userId}`;
  }

  private decimalForAsset(asset: string, atoms: bigint): string {
    return atomsToDecimal(atoms, this.getAssetDecimals(asset));
  }

  private quoteAtomsForSize(priceAtoms: bigint, sizeAtoms: bigint): bigint {
    if (!this.marketConfig) {
      throw new Error("Worker not initialized");
    }

    const baseScale = this.assetScale(this.marketConfig.baseAsset);
    const quoteScale = this.assetScale(this.marketConfig.quoteAsset);
    return (priceAtoms * sizeAtoms * quoteScale) / (PRICE_SCALE * baseScale);
  }

  private lockAssetForOrder(order: Order): string {
    if (!this.marketConfig) {
      throw new Error("Worker not initialized");
    }

    return order.side === "BUY"
      ? this.marketConfig.quoteAsset.toUpperCase()
      : this.marketConfig.baseAsset.toUpperCase();
  }

  private lockAtomsForOrder(order: Order, remainingAtoms = order.remainingAtoms): bigint {
    if (remainingAtoms <= 0n) return 0n;
    return order.side === "BUY"
      ? this.quoteAtomsForSize(order.priceAtoms, remainingAtoms)
      : remainingAtoms;
  }

  private feeAtomsForTradeQuote(quoteAtoms: bigint, feeBps: number): bigint {
    if (quoteAtoms <= 0n || feeBps <= 0) return 0n;
    const numerator = quoteAtoms * BigInt(feeBps);
    const fee = numerator / 10000n;
    return numerator % 10000n === 0n ? fee : fee + 1n;
  }

  private async getBalanceForUpdate(
    client: PoolClient,
    accountId: string,
    asset: string
  ): Promise<BalanceSnapshot | null> {
    const result = await client.query(
      `SELECT available_atoms, locked_atoms
         FROM balances
        WHERE account_id = $1
          AND UPPER(asset) = $2
        FOR UPDATE`,
      [accountId, asset.toUpperCase()]
    );

    if ((result.rowCount ?? 0) === 0) return null;

    const row = result.rows[0];
    return {
      availableAtoms: BigInt(row.available_atoms ?? 0),
      lockedAtoms: BigInt(row.locked_atoms ?? 0)
    };
  }

  private async setBalance(
    client: PoolClient,
    accountId: string,
    asset: string,
    availableAtoms: bigint,
    lockedAtoms: bigint
  ): Promise<void> {
    if (availableAtoms < 0n || lockedAtoms < 0n) {
      throw new Error(
        `negative balance update rejected for ${accountId} ${asset}: available=${availableAtoms.toString()} locked=${lockedAtoms.toString()}`
      );
    }

    const normalizedAsset = asset.toUpperCase();
    await client.query(
      `INSERT INTO balances (
         account_id, asset, available, locked, available_atoms, locked_atoms, updated_at
       ) VALUES ($1, $2, $3, $4, $5, $6, NOW())
       ON CONFLICT (account_id, asset)
       DO UPDATE SET
         available = EXCLUDED.available,
         locked = EXCLUDED.locked,
         available_atoms = EXCLUDED.available_atoms,
         locked_atoms = EXCLUDED.locked_atoms,
         updated_at = NOW()`,
      [
        accountId,
        normalizedAsset,
        this.decimalForAsset(normalizedAsset, availableAtoms),
        this.decimalForAsset(normalizedAsset, lockedAtoms),
        availableAtoms.toString(),
        lockedAtoms.toString()
      ]
    );
  }

  private async creditAvailable(
    client: PoolClient,
    accountId: string,
    asset: string,
    amountAtoms: bigint
  ): Promise<void> {
    if (amountAtoms <= 0n) return;

    const balance = await this.getBalanceForUpdate(client, accountId, asset);
    await this.setBalance(
      client,
      accountId,
      asset,
      (balance?.availableAtoms ?? 0n) + amountAtoms,
      balance?.lockedAtoms ?? 0n
    );
  }

  private async debitLocked(
    client: PoolClient,
    accountId: string,
    asset: string,
    amountAtoms: bigint,
    reason: string
  ): Promise<void> {
    if (amountAtoms <= 0n) return;

    const balance = await this.getBalanceForUpdate(client, accountId, asset);
    if (!balance || balance.lockedAtoms < amountAtoms) {
      const locked = balance?.lockedAtoms ?? 0n;
      throw new Error(
        `insufficient locked ${asset.toUpperCase()} for ${reason}: required ${this.decimalForAsset(asset, amountAtoms)}, locked ${this.decimalForAsset(asset, locked)}`
      );
    }

    await this.setBalance(
      client,
      accountId,
      asset,
      balance.availableAtoms,
      balance.lockedAtoms - amountAtoms
    );
  }

  private async moveAvailableToLocked(
    client: PoolClient,
    accountId: string,
    asset: string,
    amountAtoms: bigint,
    reason: string
  ): Promise<void> {
    if (amountAtoms <= 0n) return;

    const balance = await this.getBalanceForUpdate(client, accountId, asset);
    if (!balance || balance.availableAtoms < amountAtoms) {
      const available = balance?.availableAtoms ?? 0n;
      throw new Error(
        `insufficient available ${asset.toUpperCase()} for ${reason}: required ${this.decimalForAsset(asset, amountAtoms)}, available ${this.decimalForAsset(asset, available)}`
      );
    }

    await this.setBalance(
      client,
      accountId,
      asset,
      balance.availableAtoms - amountAtoms,
      balance.lockedAtoms + amountAtoms
    );
  }

  private async moveLockedToAvailable(
    client: PoolClient,
    accountId: string,
    asset: string,
    amountAtoms: bigint,
    reason: string
  ): Promise<void> {
    if (amountAtoms <= 0n) return;

    const balance = await this.getBalanceForUpdate(client, accountId, asset);
    if (!balance || balance.lockedAtoms < amountAtoms) {
      const locked = balance?.lockedAtoms ?? 0n;
      throw new Error(
        `insufficient locked ${asset.toUpperCase()} for ${reason}: required ${this.decimalForAsset(asset, amountAtoms)}, locked ${this.decimalForAsset(asset, locked)}`
      );
    }

    await this.setBalance(
      client,
      accountId,
      asset,
      balance.availableAtoms + amountAtoms,
      balance.lockedAtoms - amountAtoms
    );
  }

  private async reserveFundsForOrder(client: PoolClient, order: Order): Promise<void> {
    const asset = this.lockAssetForOrder(order);
    const lockAtoms = this.lockAtomsForOrder(order);
    if (lockAtoms <= 0n) {
      throw new Error(`invalid lock amount for ${order.side} order ${order.id}`);
    }

    const existingLock = await client.query(
      `SELECT locked_atoms
         FROM order_locks
        WHERE order_id = $1
        FOR UPDATE`,
      [order.id]
    );

    if ((existingLock.rowCount ?? 0) > 0) {
      const existingAtoms = BigInt(existingLock.rows[0].locked_atoms ?? 0);
      if (existingAtoms !== lockAtoms) {
        throw new Error(
          `order ${order.id} already has a different lock: existing=${existingAtoms.toString()} required=${lockAtoms.toString()}`
        );
      }
      return;
    }

    await this.moveAvailableToLocked(
      client,
      this.accountIdForUser(order.userId),
      asset,
      lockAtoms,
      `order ${order.id} reserve`
    );

    await client.query(
      `INSERT INTO order_locks (
         order_id, user_id, asset_id, locked_atoms, used_atoms, created_at, updated_at
       ) VALUES ($1, $2, $3, $4, 0, NOW(), NOW())`,
      [order.id, order.userId, asset, lockAtoms.toString()]
    );
  }

  private estimateMarketBuyQuoteAtoms(sizeAtoms: bigint): bigint {
    if (!this.engine) {
      throw new Error("Worker not initialized");
    }

    let remainingAtoms = sizeAtoms;
    let quoteAtoms = 0n;

    for (const level of this.engine.getOrderBook().getAskLevels()) {
      if (remainingAtoms <= 0n) break;
      const fillAtoms = remainingAtoms < level.totalSize ? remainingAtoms : level.totalSize;
      quoteAtoms += this.quoteAtomsForSize(level.priceAtoms, fillAtoms);
      remainingAtoms -= fillAtoms;
    }

    return quoteAtoms;
  }

  private async reserveFundsForMarketOrder(client: PoolClient, order: Order): Promise<void> {
    const asset = this.lockAssetForOrder(order);
    const lockAtoms =
      order.side === "BUY"
        ? this.estimateMarketBuyQuoteAtoms(order.remainingAtoms)
        : order.remainingAtoms;

    if (lockAtoms <= 0n) return;

    const existingLock = await client.query(
      `SELECT locked_atoms
         FROM order_locks
        WHERE order_id = $1
        FOR UPDATE`,
      [order.id]
    );

    if ((existingLock.rowCount ?? 0) > 0) return;

    await this.moveAvailableToLocked(
      client,
      this.accountIdForUser(order.userId),
      asset,
      lockAtoms,
      `market order ${order.id} reserve`
    );

    await client.query(
      `INSERT INTO order_locks (
         order_id, user_id, asset_id, locked_atoms, used_atoms, created_at, updated_at
       ) VALUES ($1, $2, $3, $4, 0, NOW(), NOW())`,
      [order.id, order.userId, asset, lockAtoms.toString()]
    );
  }

  private async addOrderLockUsage(
    client: PoolClient,
    order: Order,
    usedAtoms: bigint
  ): Promise<void> {
    if (usedAtoms <= 0n) return;

    const result = await client.query(
      `UPDATE order_locks
          SET used_atoms = used_atoms + $1::numeric,
              updated_at = NOW()
        WHERE order_id = $2
          AND used_atoms + $1::numeric <= locked_atoms`,
      [usedAtoms.toString(), order.id]
    );

    if ((result.rowCount ?? 0) !== 1) {
      throw new Error(
        `order ${order.id} lock usage exceeds reserved ${this.lockAssetForOrder(order)} funds`
      );
    }
  }

  private async releaseOrderLock(client: PoolClient, order: Order, reason: string): Promise<void> {
    const result = await client.query(
      `SELECT asset_id, locked_atoms, used_atoms
         FROM order_locks
        WHERE order_id = $1
        FOR UPDATE`,
      [order.id]
    );

    if ((result.rowCount ?? 0) === 0) return;

    const row = result.rows[0];
    const asset = String(row.asset_id).toUpperCase();
    const lockedAtoms = BigInt(row.locked_atoms ?? 0);
    const usedAtoms = BigInt(row.used_atoms ?? 0);
    const releasableAtoms = lockedAtoms - usedAtoms;

    if (releasableAtoms > 0n) {
      await this.moveLockedToAvailable(
        client,
        this.accountIdForUser(order.userId),
        asset,
        releasableAtoms,
        `${reason} release for order ${order.id}`
      );
    }

    await client.query(`DELETE FROM order_locks WHERE order_id = $1`, [order.id]);
  }

  private async releaseSurplusOrderLock(client: PoolClient, order: Order): Promise<void> {
    const result = await client.query(
      `SELECT asset_id, locked_atoms, used_atoms
         FROM order_locks
        WHERE order_id = $1
        FOR UPDATE`,
      [order.id]
    );

    if ((result.rowCount ?? 0) === 0) return;

    const row = result.rows[0];
    const asset = String(row.asset_id).toUpperCase();
    const lockedAtoms = BigInt(row.locked_atoms ?? 0);
    const usedAtoms = BigInt(row.used_atoms ?? 0);
    const remainingLockAtoms = this.lockAtomsForOrder(order);
    const currentUnspentLock = lockedAtoms - usedAtoms;
    const surplusAtoms = currentUnspentLock - remainingLockAtoms;

    if (surplusAtoms <= 0n) return;

    await this.moveLockedToAvailable(
      client,
      this.accountIdForUser(order.userId),
      asset,
      surplusAtoms,
      `price improvement release for order ${order.id}`
    );

    await client.query(
      `UPDATE order_locks
          SET locked_atoms = locked_atoms - $1::numeric,
              updated_at = NOW()
        WHERE order_id = $2`,
      [surplusAtoms.toString(), order.id]
    );
  }

  private orderPairForTrade(
    trade: { makerOrderId: string; takerOrderId: string },
    ordersById: Map<string, Order>
  ): TradeOrderPair {
    const makerOrder = ordersById.get(trade.makerOrderId);
    const takerOrder = ordersById.get(trade.takerOrderId);
    if (!makerOrder || !takerOrder) {
      throw new Error(
        `missing matched orders for trade maker=${trade.makerOrderId} taker=${trade.takerOrderId}`
      );
    }
    return { makerOrder, takerOrder };
  }

  private async settleTrade(
    client: PoolClient,
    trade: { id: string; makerOrderId: string; takerOrderId: string; priceAtoms: bigint; sizeAtoms: bigint; feeBpsMaker: number; feeBpsTaker: number },
    ordersById: Map<string, Order>
  ): Promise<void> {
    if (!this.marketConfig) {
      throw new Error("Worker not initialized");
    }

    const { makerOrder, takerOrder } = this.orderPairForTrade(trade, ordersById);
    const buyerOrder = makerOrder.side === "BUY" ? makerOrder : takerOrder;
    const sellerOrder = makerOrder.side === "SELL" ? makerOrder : takerOrder;
    const buyerIsMaker = buyerOrder.id === makerOrder.id;
    const sellerIsMaker = sellerOrder.id === makerOrder.id;

    const baseAsset = this.marketConfig.baseAsset.toUpperCase();
    const quoteAsset = this.marketConfig.quoteAsset.toUpperCase();
    const feeAsset = this.marketConfig.feeAsset.toUpperCase();
    const quoteAtoms = this.quoteAtomsForSize(trade.priceAtoms, trade.sizeAtoms);
    const buyerFeeAtoms =
      feeAsset === baseAsset
        ? this.feeAtomsForTradeQuote(quoteAtoms, buyerIsMaker ? trade.feeBpsMaker : trade.feeBpsTaker)
        : 0n;
    const sellerFeeAtoms =
      feeAsset === quoteAsset
        ? this.feeAtomsForTradeQuote(quoteAtoms, sellerIsMaker ? trade.feeBpsMaker : trade.feeBpsTaker)
        : 0n;

    if (buyerFeeAtoms > trade.sizeAtoms) {
      throw new Error(`buyer fee exceeds base credit for trade ${trade.id}`);
    }
    if (sellerFeeAtoms > quoteAtoms) {
      throw new Error(`seller fee exceeds quote credit for trade ${trade.id}`);
    }

    const buyerAccountId = this.accountIdForUser(buyerOrder.userId);
    const sellerAccountId = this.accountIdForUser(sellerOrder.userId);

    await this.debitLocked(
      client,
      sellerAccountId,
      baseAsset,
      trade.sizeAtoms,
      `trade ${trade.id} seller base debit`
    );
    await this.debitLocked(
      client,
      buyerAccountId,
      quoteAsset,
      quoteAtoms,
      `trade ${trade.id} buyer quote debit`
    );

    await this.creditAvailable(
      client,
      buyerAccountId,
      baseAsset,
      trade.sizeAtoms - buyerFeeAtoms
    );
    await this.creditAvailable(
      client,
      sellerAccountId,
      quoteAsset,
      quoteAtoms - sellerFeeAtoms
    );

    if (buyerFeeAtoms > 0n) {
      await this.creditAvailable(client, "system:fees", baseAsset, buyerFeeAtoms);
    }
    if (sellerFeeAtoms > 0n) {
      await this.creditAvailable(client, "system:fees", quoteAsset, sellerFeeAtoms);
    }

    await this.addOrderLockUsage(client, buyerOrder, quoteAtoms);
    await this.addOrderLockUsage(client, sellerOrder, trade.sizeAtoms);
  }

  private async settleTrades(
    client: PoolClient,
    trades: Array<{ id: string; makerOrderId: string; takerOrderId: string; priceAtoms: bigint; sizeAtoms: bigint; feeBpsMaker: number; feeBpsTaker: number }>,
    ordersById: Map<string, Order>
  ): Promise<void> {
    for (const trade of trades) {
      await this.settleTrade(client, trade, ordersById);
    }
  }

  private async reconcileLocksAfterMatch(
    client: PoolClient,
    orders: Iterable<Order>
  ): Promise<void> {
    for (const order of orders) {
      if (order.status === "FILLED" || order.status === "EXPIRED") {
        await this.releaseOrderLock(client, order, order.status.toLowerCase());
      } else {
        await this.releaseSurplusOrderLock(client, order);
      }
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

      const ordersRepo = new OrdersRepo(client, this.getBaseDecimals());
      const tradesRepo = new TradesRepo(client);
      const eventsRepo = new EventsRepo(client);
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

      try {
        await this.reserveFundsForOrder(client, order);
      } catch (error) {
        const reason = error instanceof Error ? error.message : String(error);
        await ordersRepo.rejectOrder(order.id, reason);
        order.status = "REJECTED";
        const rejectSeq = await sequenceRepo.nextSequence(this.marketId);
        await eventsRepo.appendEvent({
          orderId: order.id,
          marketId: this.marketId,
          eventType: "REJECTED",
          sequence: rejectSeq,
          payload: { order, reason }
        });
        const result: OrderResult = {
          success: false,
          order,
          fills: [],
          trades: [],
          events: [],
          rejectReason: reason
        };
        await idempotencyRepo.set(cmd.idempotencyKey, "matching-engine", result);
        await client.query("COMMIT");
        return result;
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
        await this.releaseOrderLock(client, order, "rejected");
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

      const ordersById = new Map<string, Order>([
        [matchResult.takerOrder.id, matchResult.takerOrder as Order]
      ]);
      for (const makerOrder of matchResult.makerUpdates.values()) {
        ordersById.set(makerOrder.id, makerOrder);
      }

      await this.settleTrades(client, matchResult.trades, ordersById);
      await this.reconcileLocksAfterMatch(client, ordersById.values());

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

      const ordersRepo = new OrdersRepo(client, this.getBaseDecimals());
      const tradesRepo = new TradesRepo(client);
      const eventsRepo = new EventsRepo(client);
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

      try {
        await this.reserveFundsForMarketOrder(client, order);
      } catch (error) {
        const reason = error instanceof Error ? error.message : String(error);
        await ordersRepo.rejectOrder(order.id, reason);
        order.status = "REJECTED";
        const rejectSeq = await sequenceRepo.nextSequence(this.marketId);
        await eventsRepo.appendEvent({
          orderId: order.id,
          marketId: this.marketId,
          eventType: "REJECTED",
          sequence: rejectSeq,
          payload: { order, reason }
        });
        const result: OrderResult = {
          success: false,
          order,
          fills: [],
          trades: [],
          events: [],
          rejectReason: reason
        };
        await idempotencyRepo.set(cmd.idempotencyKey, "matching-engine", result);
        await client.query("COMMIT");
        return result;
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

      // Match order
      let matchResult;
      try {
        matchResult = this.engine.match(order);
      } catch (error) {
        const reason = error instanceof Error ? error.message : String(error);
        await ordersRepo.rejectOrder(order.id, reason);
        order.status = "REJECTED";
        const rejectSeq = await sequenceRepo.nextSequence(this.marketId);
        await eventsRepo.appendEvent({
          orderId: order.id,
          marketId: this.marketId,
          eventType: "REJECTED",
          sequence: rejectSeq,
          payload: { order, reason }
        });
        await this.releaseOrderLock(client, order, "rejected");
        const result: OrderResult = {
          success: false,
          order,
          fills: [],
          trades: [],
          events: [],
          rejectReason: reason
        };
        await idempotencyRepo.set(cmd.idempotencyKey, "matching-engine", result);
        await client.query("COMMIT");
        return result;
      }

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

      const ordersById = new Map<string, Order>([
        [matchResult.takerOrder.id, matchResult.takerOrder as Order]
      ]);
      for (const makerOrder of matchResult.makerUpdates.values()) {
        ordersById.set(makerOrder.id, makerOrder);
      }

      await this.settleTrades(client, matchResult.trades, ordersById);
      await this.reconcileLocksAfterMatch(client, ordersById.values());

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

      const ordersRepo = new OrdersRepo(client, this.getBaseDecimals());
      const eventsRepo = new EventsRepo(client);
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

      await this.releaseOrderLock(client, order, "canceled");

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

      const ordersRepo = new OrdersRepo(client, this.getBaseDecimals());
      const eventsRepo = new EventsRepo(client);
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

      if (newSizeAtoms < this.marketConfig.minOrderSize) {
        const result: OrderResult = {
          success: false,
          order: existingOrder,
          fills: [],
          trades: [],
          events: [],
          rejectReason: "Below minimum order size"
        };
        await idempotencyRepo.set(cmd.idempotencyKey, "matching-engine", result);
        await client.query("COMMIT");
        return result;
      }

      if (postOnly && this.engine.getOrderBook().wouldCross(existingOrder.side, newPriceAtoms)) {
        const result: OrderResult = {
          success: false,
          order: existingOrder,
          fills: [],
          trades: [],
          events: [],
          rejectReason: "Post-only order would cross"
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

      await this.releaseOrderLock(client, existingOrder, "replaced");

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

      try {
        if (newOrder.orderType === "MARKET") {
          await this.reserveFundsForMarketOrder(client, newOrder);
        } else {
          await this.reserveFundsForOrder(client, newOrder);
        }
      } catch (error) {
        const reason = error instanceof Error ? error.message : String(error);
        await ordersRepo.rejectOrder(newOrder.id, reason);
        newOrder.status = "REJECTED";
        const rejectSeq = await sequenceRepo.nextSequence(this.marketId);
        await eventsRepo.appendEvent({
          orderId: newOrder.id,
          marketId: this.marketId,
          eventType: "REJECTED",
          sequence: rejectSeq,
          payload: { order: newOrder, replacedFrom: existingOrder.id, reason }
        });
        const result: OrderResult = {
          success: false,
          order: newOrder,
          fills: [],
          trades: [],
          events: [],
          rejectReason: reason
        };
        await idempotencyRepo.set(cmd.idempotencyKey, "matching-engine", result);
        await client.query("COMMIT");
        return result;
      }

      // Write accepted event
      const acceptSeq = await sequenceRepo.nextSequence(this.marketId);
      await eventsRepo.appendEvent({
        orderId: newOrder.id,
        marketId: this.marketId,
        eventType: "ACCEPTED",
        sequence: acceptSeq,
        payload: { order: newOrder, replacedFrom: existingOrder.id }
      });

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
