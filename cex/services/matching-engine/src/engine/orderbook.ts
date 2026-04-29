/**
 * In-memory orderbook with strict price-time priority
 * Deterministic FIFO within each price level
 */

import {
  compareOrders,
  compareBidPrices,
  compareAskPrices
} from "./deterministic.js";
import type { Order, PriceLevel } from "./types.js";

export class OrderBook {
  private bids: Map<bigint, Order[]> = new Map(); // price -> orders (descending)
  private asks: Map<bigint, Order[]> = new Map(); // price -> orders (ascending)
  private orderIndex: Map<string, { price: bigint; side: "BUY" | "SELL" }> = new Map();

  /**
   * Add an order to the book
   */
  add(order: any): void {
    if (order.remainingAtoms <= 0n) {
      throw new Error("Cannot add order with no remaining size");
    }

    const book = order.side === "BUY" ? this.bids : this.asks;
    const orders = book.get(order.priceAtoms) || [];
    orders.push(order);
    book.set(order.priceAtoms, orders);

    this.orderIndex.set(order.id, { price: order.priceAtoms, side: order.side });
  }

  /**
   * Remove an order from the book
   */
  remove(orderId: string): any | undefined {
    const index = this.orderIndex.get(orderId);
    if (!index) return undefined;

    const book = index.side === "BUY" ? this.bids : this.asks;
    const orders = book.get(index.price);
    if (!orders) return undefined;

    const orderIdx = orders.findIndex((o) => o.id === orderId);
    if (orderIdx === -1) return undefined;

    const [removed] = orders.splice(orderIdx, 1);
    this.orderIndex.delete(orderId);

    // Clean up empty price levels
    if (orders.length === 0) {
      book.delete(index.price);
    }

    return removed;
  }

  /**
   * Get an order by ID
   */
  get(orderId: string): any | undefined {
    const index = this.orderIndex.get(orderId);
    if (!index) return undefined;

    const book = index.side === "BUY" ? this.bids : this.asks;
    const orders = book.get(index.price);
    return orders?.find((o) => o.id === orderId);
  }

  /**
   * Get the best bid (highest price)
   */
  getBestBid(): any | undefined {
    if (this.bids.size === 0) return undefined;

    const prices = Array.from(this.bids.keys()).sort(compareBidPrices);
    for (const price of prices) {
      const orders = this.bids.get(price);
      if (orders && orders.length > 0) {
        // Sort by FIFO (accepted_at, then order_id)
        orders.sort(compareOrders);
        return orders[0];
      }
    }

    return undefined;
  }

  /**
   * Get the best ask (lowest price)
   */
  getBestAsk(): any | undefined {
    if (this.asks.size === 0) return undefined;

    const prices = Array.from(this.asks.keys()).sort(compareAskPrices);
    for (const price of prices) {
      const orders = this.asks.get(price);
      if (orders && orders.length > 0) {
        // Sort by FIFO (accepted_at, then order_id)
        orders.sort(compareOrders);
        return orders[0];
      }
    }

    return undefined;
  }

  /**
   * Get all bids at a price level (sorted FIFO)
   */
  getBidsAtPrice(priceAtoms: bigint): any[] {
    const orders = this.bids.get(priceAtoms) || [];
    return orders.slice().sort(compareOrders);
  }

  /**
   * Get all asks at a price level (sorted FIFO)
   */
  getAsksAtPrice(priceAtoms: bigint): any[] {
    const orders = this.asks.get(priceAtoms) || [];
    return orders.slice().sort(compareOrders);
  }

  /**
   * Get all price levels for bids (sorted best first)
   */
  getBidLevels(): PriceLevel[] {
    const prices = Array.from(this.bids.keys()).sort(compareBidPrices);
    return prices.map((priceAtoms) => {
      const orders = this.getBidsAtPrice(priceAtoms);
      const totalSize = orders.reduce((sum, o) => sum + o.remainingAtoms, 0n);
      return { priceAtoms, orders, totalSize };
    });
  }

  /**
   * Get all ask levels (sorted best first)
   */
  getAskLevels(): PriceLevel[] {
    const prices = Array.from(this.asks.keys()).sort(compareAskPrices);
    return prices.map((priceAtoms) => {
      const orders = this.getAsksAtPrice(priceAtoms);
      const totalSize = orders.reduce((sum, o) => sum + o.remainingAtoms, 0n);
      return { priceAtoms, orders, totalSize };
    });
  }

  /**
   * Check if book would cross (for post-only validation)
   */
  wouldCross(side: "BUY" | "SELL", priceAtoms: bigint): boolean {
    if (side === "BUY") {
      const bestAsk = this.getBestAsk();
      // Buy crosses if bid price >= best ask price
      return bestAsk !== undefined && priceAtoms >= bestAsk.priceAtoms;
    } else {
      const bestBid = this.getBestBid();
      // Sell crosses if ask price <= best bid price
      return bestBid !== undefined && priceAtoms <= bestBid.priceAtoms;
    }
  }

  /**
   * Get snapshot of the book (for debugging/testing)
   */
  snapshot(): { bids: PriceLevel[]; asks: PriceLevel[] } {
    return {
      bids: this.getBidLevels(),
      asks: this.getAskLevels()
    };
  }

  /**
   * Clear the book
   */
  clear(): void {
    this.bids.clear();
    this.asks.clear();
    this.orderIndex.clear();
  }

  /**
   * Get total number of orders in the book
   */
  size(): number {
    return this.orderIndex.size;
  }
}
