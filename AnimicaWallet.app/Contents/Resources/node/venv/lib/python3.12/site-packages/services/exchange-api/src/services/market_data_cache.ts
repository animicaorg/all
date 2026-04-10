/**
 * In-Memory Market Data Cache
 * 
 * Maintains real-time orderbook state and ticker data for all trading pairs.
 * Handles incremental updates via sequence numbers to prevent data races.
 */

export interface OrderBookLevel {
  price: string;
  quantity: string;
}

export interface OrderBookSnapshot {
  market: string;
  sequence: number;
  bids: OrderBookLevel[];
  asks: OrderBookLevel[];
  timestamp: number;
}

export interface OrderBookDiff {
  bids?: OrderBookLevel[];
  asks?: OrderBookLevel[];
}

export interface Ticker {
  market: string;
  lastPrice: string;
  volume24h: string;
  high24h: string;
  low24h: string;
  priceChange24h: string;
  priceChangePercent24h: string;
  timestamp: number;
}

interface OrderBookState {
  sequence: number;
  bids: Map<string, string>; // price -> quantity
  asks: Map<string, string>; // price -> quantity
  lastUpdate: number;
}

interface TickerState extends Ticker {}

export class MarketDataCache {
  private orderbooks: Map<string, OrderBookState> = new Map();
  private tickers: Map<string, TickerState> = new Map();

  /**
   * Get a snapshot of the orderbook for a given market
   * @param market - Market symbol (e.g., "BTC_USD")
   * @param depth - Maximum number of levels to return per side
   * @returns OrderBook snapshot with current sequence number
   */
  getSnapshot(market: string, depth: number = 20): OrderBookSnapshot | null {
    const state = this.orderbooks.get(market);
    if (!state) {
      return null;
    }

    // Sort bids descending (highest first), asks ascending (lowest first)
    const bids = Array.from(state.bids.entries())
      .map(([price, quantity]) => ({ price, quantity }))
      .sort((a, b) => parseFloat(b.price) - parseFloat(a.price))
      .slice(0, depth);

    const asks = Array.from(state.asks.entries())
      .map(([price, quantity]) => ({ price, quantity }))
      .sort((a, b) => parseFloat(a.price) - parseFloat(b.price))
      .slice(0, depth);

    return {
      market,
      sequence: state.sequence,
      bids,
      asks,
      timestamp: state.lastUpdate,
    };
  }

  /**
   * Apply an incremental orderbook update
   * @param market - Market symbol
   * @param diff - Orderbook changes (price levels with quantity 0 are removals)
   * @param sequence - Sequence number of this update
   * @returns true if applied, false if sequence is stale
   */
  applyDiff(market: string, diff: OrderBookDiff, sequence: number): boolean {
    let state = this.orderbooks.get(market);

    // Initialize if doesn't exist
    if (!state) {
      state = {
        sequence: 0,
        bids: new Map(),
        asks: new Map(),
        lastUpdate: Date.now(),
      };
      this.orderbooks.set(market, state);
    }

    // Reject stale updates
    if (sequence <= state.sequence) {
      return false;
    }

    // Apply bid updates
    if (diff.bids) {
      for (const { price, quantity } of diff.bids) {
        if (parseFloat(quantity) === 0) {
          state.bids.delete(price);
        } else {
          state.bids.set(price, quantity);
        }
      }
    }

    // Apply ask updates
    if (diff.asks) {
      for (const { price, quantity } of diff.asks) {
        if (parseFloat(quantity) === 0) {
          state.asks.delete(price);
        } else {
          state.asks.set(price, quantity);
        }
      }
    }

    state.sequence = sequence;
    state.lastUpdate = Date.now();

    return true;
  }

  /**
   * Set a full orderbook snapshot (used for initialization)
   */
  setSnapshot(market: string, snapshot: OrderBookSnapshot): void {
    const state: OrderBookState = {
      sequence: snapshot.sequence,
      bids: new Map(snapshot.bids.map(l => [l.price, l.quantity])),
      asks: new Map(snapshot.asks.map(l => [l.price, l.quantity])),
      lastUpdate: snapshot.timestamp,
    };
    this.orderbooks.set(market, state);
  }

  /**
   * Get ticker data for a market
   */
  getTicker(market: string): Ticker | null {
    return this.tickers.get(market) || null;
  }

  /**
   * Update ticker data for a market
   */
  updateTicker(market: string, ticker: Ticker): void {
    this.tickers.set(market, { ...ticker });
  }

  /**
   * Get all available markets
   */
  getMarkets(): string[] {
    return Array.from(this.orderbooks.keys());
  }

  /**
   * Clear all cached data
   */
  clear(): void {
    this.orderbooks.clear();
    this.tickers.clear();
  }

  /**
   * Remove a specific market from cache
   */
  removeMarket(market: string): void {
    this.orderbooks.delete(market);
    this.tickers.delete(market);
  }
}
