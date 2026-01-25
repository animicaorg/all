/**
 * Incremental Diff Streaming
 * 
 * Handles streaming of incremental updates (diffs) for subscribed channels.
 * Includes sequence number tracking and gap detection for reliable delivery.
 */

import type { Logger } from '../utils/logger.js';
import type { MarketDataCache, OrderBookDiff } from '../services/market_data_cache.js';
import type {
  OrderbookUpdateMessage,
  TradeMessage,
  TickerMessage,
} from './protocol.js';

/**
 * Sequence tracker for detecting gaps in orderbook updates
 */
export class SequenceTracker {
  // Map: market -> last sequence number seen
  private sequences: Map<string, number> = new Map();

  /**
   * Update sequence for a market
   * @returns true if sequence is valid, false if there's a gap
   */
  update(market: string, seq: number): boolean {
    const lastSeq = this.sequences.get(market);

    if (lastSeq === undefined) {
      // First sequence for this market
      this.sequences.set(market, seq);
      return true;
    }

    // Check for gap (sequence should increment by 1)
    if (seq !== lastSeq + 1) {
      // Gap detected
      this.sequences.set(market, seq); // Update to new sequence anyway
      return false;
    }

    this.sequences.set(market, seq);
    return true;
  }

  /**
   * Reset sequence for a market (after resubscribe)
   */
  reset(market: string): void {
    this.sequences.delete(market);
  }

  /**
   * Get last sequence for a market
   */
  get(market: string): number | undefined {
    return this.sequences.get(market);
  }

  /**
   * Clear all sequences
   */
  clear(): void {
    this.sequences.clear();
  }
}

/**
 * Ticker throttle to limit ticker update frequency
 */
export class TickerThrottle {
  private lastSent: Map<string, number> = new Map();
  private readonly throttleMs: number;

  constructor(throttleMs: number = 1000) {
    this.throttleMs = throttleMs;
  }

  /**
   * Check if ticker update should be sent
   * @returns true if enough time has passed since last send
   */
  shouldSend(market: string): boolean {
    const now = Date.now();
    const last = this.lastSent.get(market);

    if (!last || now - last >= this.throttleMs) {
      this.lastSent.set(market, now);
      return true;
    }

    return false;
  }

  /**
   * Force update last sent time
   */
  markSent(market: string): void {
    this.lastSent.set(market, Date.now());
  }

  /**
   * Clear throttle state
   */
  clear(): void {
    this.lastSent.clear();
  }
}

/**
 * Creates an orderbook update message from a diff
 */
export function createOrderbookUpdate(
  market: string,
  seq: number,
  diff: OrderBookDiff,
  timestamp: number
): OrderbookUpdateMessage {
  const changes: OrderbookUpdateMessage['changes'] = {};

  if (diff.bids && diff.bids.length > 0) {
    changes.bids = diff.bids.map(b => [b.price, b.quantity] as [string, string]);
  }

  if (diff.asks && diff.asks.length > 0) {
    changes.asks = diff.asks.map(a => [a.price, a.quantity] as [string, string]);
  }

  return {
    type: 'update',
    channel: 'book',
    market,
    seq,
    changes,
    ts: timestamp,
  };
}

/**
 * Streams orderbook diff to subscribers
 * 
 * @param market - Market symbol
 * @param diff - Orderbook changes
 * @param seq - Sequence number
 * @param marketDataCache - Market data cache
 * @param broadcast - Function to broadcast message to subscribers
 * @param logger - Logger instance
 */
export function streamOrderbookDiff(
  market: string,
  diff: OrderBookDiff,
  seq: number,
  marketDataCache: MarketDataCache,
  broadcast: (channelKey: string, msg: OrderbookUpdateMessage) => void,
  logger: Logger
): void {
  // Apply diff to cache first
  const applied = marketDataCache.applyDiff(market, diff, seq);

  if (!applied) {
    logger.warn(
      { market, seq },
      'Failed to apply orderbook diff (stale sequence)'
    );
    return;
  }

  // Create update message
  const message = createOrderbookUpdate(market, seq, diff, Date.now());

  // Broadcast to subscribers
  const channelKey = `book:${market}`;
  broadcast(channelKey, message);

  logger.debug(
    {
      market,
      seq,
      bidsChanged: diff.bids?.length || 0,
      asksChanged: diff.asks?.length || 0,
    },
    'Orderbook diff streamed'
  );
}

/**
 * Streams a trade to subscribers
 */
export function streamTrade(
  trade: TradeMessage,
  broadcast: (channelKey: string, msg: TradeMessage) => void,
  logger: Logger
): void {
  const channelKey = `trades:${trade.market}`;
  broadcast(channelKey, trade);

  logger.debug(
    {
      market: trade.market,
      tradeId: trade.trade_id,
      price: trade.price,
      size: trade.size,
    },
    'Trade streamed'
  );
}

/**
 * Streams a ticker update to subscribers (with throttling)
 */
export function streamTicker(
  ticker: TickerMessage,
  throttle: TickerThrottle,
  broadcast: (channelKey: string, msg: TickerMessage) => void,
  logger: Logger
): void {
  // Check if we should send (throttled)
  if (!throttle.shouldSend(ticker.market)) {
    logger.trace({ market: ticker.market }, 'Ticker update throttled');
    return;
  }

  const channelKey = `tickers:${ticker.market}`;
  broadcast(channelKey, ticker);

  logger.debug({ market: ticker.market }, 'Ticker update streamed');
}

/**
 * Handles sequence gap detection and notifies client to resubscribe
 */
export function handleSequenceGap(
  market: string,
  expectedSeq: number,
  receivedSeq: number,
  sendError: (code: string, message: string) => void,
  logger: Logger
): void {
  logger.warn(
    { market, expectedSeq, receivedSeq },
    'Sequence gap detected in orderbook stream'
  );

  sendError(
    'SEQUENCE_GAP',
    `Sequence gap detected for ${market}. Expected ${expectedSeq}, got ${receivedSeq}. Please resubscribe.`
  );
}

/**
 * Utility to check if a diff has any changes
 */
export function hasDiffChanges(diff: OrderBookDiff): boolean {
  return (
    (diff.bids !== undefined && diff.bids.length > 0) ||
    (diff.asks !== undefined && diff.asks.length > 0)
  );
}

/**
 * Validates orderbook diff before streaming
 */
export function validateOrderbookDiff(
  market: string,
  diff: OrderBookDiff,
  seq: number,
  logger: Logger
): boolean {
  if (!market) {
    logger.warn('Market missing in orderbook diff');
    return false;
  }

  if (seq <= 0) {
    logger.warn({ market, seq }, 'Invalid sequence number in orderbook diff');
    return false;
  }

  if (!hasDiffChanges(diff)) {
    logger.debug({ market, seq }, 'Empty orderbook diff (no changes)');
    return false;
  }

  return true;
}

/**
 * Validates trade message before streaming
 */
export function validateTrade(trade: TradeMessage, logger: Logger): boolean {
  if (!trade.market || !trade.trade_id || !trade.price || !trade.size) {
    logger.warn({ trade }, 'Invalid trade message (missing required fields)');
    return false;
  }

  if (!['buy', 'sell'].includes(trade.side)) {
    logger.warn({ trade }, 'Invalid trade side');
    return false;
  }

  return true;
}

/**
 * Validates ticker message before streaming
 */
export function validateTicker(ticker: TickerMessage, logger: Logger): boolean {
  if (!ticker.market || !ticker.last) {
    logger.warn({ ticker }, 'Invalid ticker message (missing required fields)');
    return false;
  }

  return true;
}
