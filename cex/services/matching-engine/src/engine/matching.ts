/**
 * Matching engine core logic
 * Implements deterministic order matching with price-time priority
 */

import { v4 as uuidv4 } from "uuid";
import { OrderBook } from "./orderbook.js";
import { calculateQuoteAmount, generateTradeId } from "./deterministic.js";
import { calculateTradeFees } from "./fees.js";
import type {
  Order,
  Trade,
  Fill,
  MarketConfig,
  OrderType,
  TimeInForce,
  OrderSide
} from "./types.js";

export interface MatchResult {
  fills: Fill[];
  trades: Trade[];
  makerUpdates: Map<string, Order>;
  takerOrder: any;
}

export class MatchingEngine {
  private orderBook: OrderBook;
  private marketConfig: MarketConfig;
  private sequence: bigint;

  constructor(marketConfig: MarketConfig, initialSequence: bigint = 0n) {
    this.orderBook = new OrderBook();
    this.marketConfig = marketConfig;
    this.sequence = initialSequence;
  }

  /**
   * Get next sequence number
   */
  private nextSequence(): bigint {
    return ++this.sequence;
  }

  /**
   * Get current orderbook
   */
  getOrderBook(): OrderBook {
    return this.orderBook;
  }

  /**
   * Add an order to the book without matching
   */
  addOrder(order: any): void {
    this.orderBook.add(order);
  }

  /**
   * Remove an order from the book
   */
  removeOrder(orderId: string): any | undefined {
    return this.orderBook.remove(orderId);
  }

  /**
   * Match a taker order against the book
   * Returns fills, trades, and updated orders
   */
  match(takerOrder: any): MatchResult {
    const fills: Fill[] = [];
    const trades: Trade[] = [];
    const makerUpdates = new Map<string, Order>();

    // Validate tick/step
    if (takerOrder.orderType === "LIMIT") {
      if (takerOrder.priceAtoms % this.marketConfig.priceTick !== 0n) {
        throw new Error("Invalid price tick");
      }
    }
    if (takerOrder.sizeAtoms % this.marketConfig.sizeStep !== 0n) {
      throw new Error("Invalid size step");
    }
    if (takerOrder.sizeAtoms < this.marketConfig.minOrderSize) {
      throw new Error("Below minimum order size");
    }

    // Check post-only
    if (takerOrder.postOnly && takerOrder.orderType === "LIMIT") {
      if (this.orderBook.wouldCross(takerOrder.side, takerOrder.priceAtoms)) {
        throw new Error("Post-only order would cross");
      }
    }

    let remaining = takerOrder.remainingAtoms;

    while (remaining > 0n) {
      const makerOrder = this.getBestMatchingOrder(takerOrder);
      if (!makerOrder) break;

      // Check if limit order price allows matching
      if (takerOrder.orderType === "LIMIT") {
        if (takerOrder.side === "BUY" && makerOrder.priceAtoms > takerOrder.priceAtoms) {
          break;
        }
        if (takerOrder.side === "SELL" && makerOrder.priceAtoms < takerOrder.priceAtoms) {
          break;
        }
      }

      // Calculate fill size (cannot exceed either order's remaining)
      const fillSize = remaining < makerOrder.remainingAtoms ? remaining : makerOrder.remainingAtoms;

      // Create fill
      const fill = this.createFill(makerOrder, takerOrder, fillSize);
      fills.push(fill);

      // Create trade
      const trade = this.createTrade(makerOrder, takerOrder, fill);
      trades.push(trade);

      // Update maker order
      makerOrder.filledAtoms += fillSize;
      makerOrder.remainingAtoms -= fillSize;
      makerUpdates.set(makerOrder.id, makerOrder);

      // Update taker order
      takerOrder.filledAtoms += fillSize;
      remaining -= fillSize;

      // Remove or update maker in book
      if (makerOrder.remainingAtoms === 0n) {
        this.orderBook.remove(makerOrder.id);
        makerOrder.status = "FILLED";
      } else {
        makerOrder.status = "PARTIAL_FILL";
      }

      // Check IOC/FOK
      if (takerOrder.timeInForce === "IOC" && remaining > 0n) {
        // IOC: cancel remaining
        break;
      }
      if (takerOrder.timeInForce === "FOK" && remaining > 0n) {
        // FOK: reject entire order (caller should handle rollback)
        throw new Error("FOK order cannot be fully filled");
      }
    }

    // Update taker status
    takerOrder.remainingAtoms = remaining;
    if (remaining === 0n) {
      takerOrder.status = "FILLED";
    } else if (takerOrder.filledAtoms > 0n) {
      takerOrder.status = "PARTIAL_FILL";
    }

    return { fills, trades, makerUpdates, takerOrder };
  }

  /**
   * Get the best matching order from the book
   */
  private getBestMatchingOrder(takerOrder: any): any | undefined {
    if (takerOrder.side === "BUY") {
      return this.orderBook.getBestAsk();
    } else {
      return this.orderBook.getBestBid();
    }
  }

  /**
   * Create a fill from a match
   */
  private createFill(makerOrder: any, takerOrder: any, fillSize: bigint): Fill {
    const priceAtoms = makerOrder.priceAtoms; // Maker price wins
    const quoteAmountAtoms = calculateQuoteAmount(priceAtoms, fillSize, 8);
    const fees = calculateTradeFees(quoteAmountAtoms, this.marketConfig);

    return {
      makerOrderId: makerOrder.id,
      takerOrderId: takerOrder.id,
      priceAtoms,
      sizeAtoms: fillSize,
      makerFeeAtoms: fees.makerFeeAtoms,
      takerFeeAtoms: fees.takerFeeAtoms
    };
  }

  /**
   * Create a trade record from a fill
   */
  private createTrade(makerOrder: any, takerOrder: any, fill: Fill): Trade {
    const sequence = this.nextSequence();
    const quoteAmountAtoms = calculateQuoteAmount(fill.priceAtoms, fill.sizeAtoms, 8);
    const fees = calculateTradeFees(quoteAmountAtoms, this.marketConfig);

    return {
      id: generateTradeId(
        this.marketConfig.id,
        sequence,
        makerOrder.id,
        takerOrder.id
      ),
      marketId: this.marketConfig.id,
      makerOrderId: makerOrder.id,
      takerOrderId: takerOrder.id,
      priceAtoms: fill.priceAtoms,
      sizeAtoms: fill.sizeAtoms,
      quoteAmountAtoms,
      makerFeeAtoms: fees.makerFeeAtoms,
      takerFeeAtoms: fees.takerFeeAtoms,
      feeAsset: fees.feeAsset,
      feeBpsMaker: fees.feeBpsMaker,
      feeBpsTaker: fees.feeBpsTaker,
      sequence,
      createdAt: new Date()
    };
  }

  /**
   * Rebuild orderbook from a list of open orders
   * Orders must be sorted by accepted_at, then order_id
   */
  rebuildFromOrders(orders: any[]): void {
    this.orderBook.clear();
    for (const order of orders) {
      if (order.remainingAtoms > 0n) {
        this.orderBook.add(order);
      }
    }
  }

  /**
   * Get current sequence
   */
  getCurrentSequence(): bigint {
    return this.sequence;
  }

  /**
   * Set sequence (for recovery)
   */
  setSequence(seq: bigint): void {
    this.sequence = seq;
  }
}
