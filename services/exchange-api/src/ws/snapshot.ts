/**
 * Snapshot Delivery
 * 
 * Handles sending initial snapshots when clients subscribe to channels.
 * Provides full state for orderbooks, recent trades, and current tickers.
 */

import type { Logger } from '../utils/logger.js';
import type { MarketDataCache } from '../services/market_data_cache.js';
import type { PrismaClient } from '@prisma/client';
import type {
  ChannelConfig,
  OrderbookSnapshotMessage,
  TradeMessage,
  TickerMessage,
} from './protocol.js';
import { CHANNEL_TYPES } from './protocol.js';

export interface SnapshotOptions {
  orderbookDepth?: number;
  recentTradesLimit?: number;
}

const DEFAULT_ORDERBOOK_DEPTH = 20;
const DEFAULT_RECENT_TRADES_LIMIT = 50;

/**
 * Sends orderbook snapshot for a market
 */
export async function sendOrderbookSnapshot(
  channel: ChannelConfig,
  marketDataCache: MarketDataCache,
  sendMessage: (msg: OrderbookSnapshotMessage) => void,
  options: SnapshotOptions,
  logger: Logger
): Promise<boolean> {
  if (!channel.market) {
    logger.warn('Market parameter missing for orderbook snapshot');
    return false;
  }

  const depth = options.orderbookDepth || DEFAULT_ORDERBOOK_DEPTH;
  const snapshot = marketDataCache.getSnapshot(channel.market, depth);

  if (!snapshot) {
    logger.warn({ market: channel.market }, 'No orderbook data available');
    return false;
  }

  // Convert to wire format
  const message: OrderbookSnapshotMessage = {
    type: 'snapshot',
    channel: 'book',
    market: snapshot.market,
    seq: snapshot.sequence,
    bids: snapshot.bids.map(b => [b.price, b.quantity] as [string, string]),
    asks: snapshot.asks.map(a => [a.price, a.quantity] as [string, string]),
    ts: snapshot.timestamp,
  };

  sendMessage(message);

  logger.debug(
    {
      market: channel.market,
      seq: snapshot.sequence,
      bids: snapshot.bids.length,
      asks: snapshot.asks.length,
    },
    'Orderbook snapshot sent'
  );

  return true;
}

/**
 * Sends recent trades for a market
 */
export async function sendRecentTrades(
  channel: ChannelConfig,
  prisma: PrismaClient,
  sendMessage: (msg: TradeMessage) => void,
  options: SnapshotOptions,
  logger: Logger
): Promise<boolean> {
  if (!channel.market) {
    logger.warn('Market parameter missing for recent trades');
    return false;
  }

  const limit = options.recentTradesLimit || DEFAULT_RECENT_TRADES_LIMIT;

  try {
    // Fetch recent trades from database
    const trades = await prisma.trade.findMany({
      where: {
        market: channel.market,
      },
      orderBy: {
        timestamp: 'desc',
      },
      take: limit,
    });

    // Send in chronological order (oldest first)
    const chronologicalTrades = trades.reverse();

    for (const trade of chronologicalTrades) {
      const message: TradeMessage = {
        type: 'trade',
        market: trade.market,
        trade_id: trade.id,
        price: trade.price.toString(),
        size: trade.quantity.toString(),
        side: trade.side.toLowerCase() as 'buy' | 'sell',
        ts: trade.timestamp.getTime(),
      };
      sendMessage(message);
    }

    logger.debug(
      { market: channel.market, count: trades.length },
      'Recent trades sent'
    );

    return true;
  } catch (error) {
    logger.error({ error, market: channel.market }, 'Failed to fetch recent trades');
    return false;
  }
}

/**
 * Sends current ticker for a market
 */
export async function sendTickerSnapshot(
  channel: ChannelConfig,
  marketDataCache: MarketDataCache,
  sendMessage: (msg: TickerMessage) => void,
  logger: Logger
): Promise<boolean> {
  if (!channel.market) {
    logger.warn('Market parameter missing for ticker snapshot');
    return false;
  }

  const ticker = marketDataCache.getTicker(channel.market);

  if (!ticker) {
    logger.warn({ market: channel.market }, 'No ticker data available');
    return false;
  }

  const message: TickerMessage = {
    type: 'ticker',
    market: ticker.market,
    last: ticker.lastPrice,
    bid: '', // Would need to get from orderbook
    ask: '', // Would need to get from orderbook
    volume: ticker.volume24h,
    high: ticker.high24h,
    low: ticker.low24h,
    change: ticker.priceChange24h,
    change_percent: ticker.priceChangePercent24h,
    ts: ticker.timestamp,
  };

  // Optionally enhance with bid/ask from orderbook
  const snapshot = marketDataCache.getSnapshot(channel.market, 1);
  if (snapshot) {
    if (snapshot.bids.length > 0) {
      message.bid = snapshot.bids[0].price;
    }
    if (snapshot.asks.length > 0) {
      message.ask = snapshot.asks[0].price;
    }
  }

  sendMessage(message);

  logger.debug({ market: channel.market }, 'Ticker snapshot sent');

  return true;
}

/**
 * Sends appropriate snapshot based on channel type
 */
export async function sendSnapshot(
  channel: ChannelConfig,
  marketDataCache: MarketDataCache,
  prisma: PrismaClient,
  sendMessage: (msg: any) => void,
  options: SnapshotOptions,
  logger: Logger
): Promise<boolean> {
  try {
    switch (channel.name) {
      case CHANNEL_TYPES.BOOK:
        return await sendOrderbookSnapshot(
          channel,
          marketDataCache,
          sendMessage,
          options,
          logger
        );

      case CHANNEL_TYPES.TRADES:
        return await sendRecentTrades(
          channel,
          prisma,
          sendMessage,
          options,
          logger
        );

      case CHANNEL_TYPES.TICKERS:
        return await sendTickerSnapshot(
          channel,
          marketDataCache,
          sendMessage,
          logger
        );

      case CHANNEL_TYPES.CANDLES:
        // Candles could fetch last N candles from DB
        // For now, no snapshot - client will get next candle update
        logger.debug({ channel: channel.name }, 'No snapshot for candles channel');
        return true;

      case CHANNEL_TYPES.ORDERS:
        // Private: could send user's open orders
        // For now, no snapshot - client will get updates
        logger.debug({ channel: channel.name }, 'No snapshot for orders channel');
        return true;

      case CHANNEL_TYPES.BALANCES:
        // Private: could send user's current balances
        // For now, no snapshot - client will get updates
        logger.debug({ channel: channel.name }, 'No snapshot for balances channel');
        return true;

      default:
        logger.warn({ channel: channel.name }, 'Unknown channel type for snapshot');
        return false;
    }
  } catch (error) {
    logger.error({ error, channel }, 'Failed to send snapshot');
    return false;
  }
}

/**
 * Sends snapshots for multiple channels
 */
export async function sendSnapshots(
  channels: ChannelConfig[],
  marketDataCache: MarketDataCache,
  prisma: PrismaClient,
  sendMessage: (msg: any) => void,
  options: SnapshotOptions,
  logger: Logger
): Promise<{ successful: number; failed: number }> {
  let successful = 0;
  let failed = 0;

  for (const channel of channels) {
    const result = await sendSnapshot(
      channel,
      marketDataCache,
      prisma,
      sendMessage,
      options,
      logger
    );

    if (result) {
      successful++;
    } else {
      failed++;
    }
  }

  return { successful, failed };
}
