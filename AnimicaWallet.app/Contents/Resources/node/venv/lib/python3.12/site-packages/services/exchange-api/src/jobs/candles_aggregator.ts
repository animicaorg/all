/**
 * Candles Aggregator Job
 * Aggregates trade events into OHLCV candles
 */

import type { PrismaClient, CandleInterval } from '@prisma/client';
import type { Logger } from '../utils/logger.js';
import type { Config } from '../config.js';
import { CandlesRepository } from '../db/repositories/candles_repo.js';
import { MarketsRepository } from '../db/repositories/markets_repo.js';

interface TradeEvent {
  marketId: string;
  price: string;
  quantity: string;
  executedAt: Date;
}

interface CandleState {
  openTime: Date;
  closeTime: Date;
  open: string;
  high: string;
  low: string;
  close: string;
  volume: string;
  quoteVolume: string;
  trades: number;
}

/**
 * Time intervals in milliseconds
 */
const INTERVALS: Record<CandleInterval, number> = {
  ONE_MINUTE: 60 * 1000,
  FIVE_MINUTES: 5 * 60 * 1000,
  FIFTEEN_MINUTES: 15 * 60 * 1000,
  ONE_HOUR: 60 * 60 * 1000,
  FOUR_HOURS: 4 * 60 * 60 * 1000,
  ONE_DAY: 24 * 60 * 60 * 1000,
};

/**
 * Calculate open time for a candle given a timestamp and interval
 */
function getOpenTime(timestamp: Date, intervalMs: number): Date {
  const ms = timestamp.getTime();
  const openMs = Math.floor(ms / intervalMs) * intervalMs;
  return new Date(openMs);
}

/**
 * Candles Aggregator
 * Listens to trade events and aggregates them into candles
 */
export class CandlesAggregator {
  private candlesRepo: CandlesRepository;
  private marketsRepo: MarketsRepository;
  private activeCandlesCache: Map<string, Map<CandleInterval, CandleState>>;
  private flushInterval: NodeJS.Timeout | null = null;

  constructor(
    private prisma: PrismaClient,
    private config: Config,
    private logger: Logger
  ) {
    this.candlesRepo = new CandlesRepository(prisma);
    this.marketsRepo = new MarketsRepository(prisma);
    this.activeCandlesCache = new Map();
  }

  /**
   * Start the aggregator
   */
  async start(): Promise<void> {
    this.logger.info('Starting candles aggregator');

    // Flush candles periodically (every 10 seconds)
    this.flushInterval = setInterval(() => {
      this.flushCandles().catch((error) => {
        this.logger.error({ error }, 'Failed to flush candles');
      });
    }, 10000);

    // TODO: Subscribe to trade events from matching engine
    // For now, we'll poll recent trades from database
    await this.pollRecentTrades();
  }

  /**
   * Stop the aggregator
   */
  async stop(): Promise<void> {
    this.logger.info('Stopping candles aggregator');

    if (this.flushInterval) {
      clearInterval(this.flushInterval);
      this.flushInterval = null;
    }

    // Flush any remaining candles
    await this.flushCandles();
  }

  /**
   * Process a trade event
   */
  async processTrade(trade: TradeEvent): Promise<void> {
    const price = parseFloat(trade.price);
    const quantity = parseFloat(trade.quantity);
    const quoteAmount = price * quantity;

    // Process for all intervals
    for (const [interval, intervalMs] of Object.entries(INTERVALS)) {
      const openTime = getOpenTime(trade.executedAt, intervalMs);
      const closeTime = new Date(openTime.getTime() + intervalMs);

      // Get or create candle state
      let marketCandles = this.activeCandlesCache.get(trade.marketId);
      if (!marketCandles) {
        marketCandles = new Map();
        this.activeCandlesCache.set(trade.marketId, marketCandles);
      }

      let candle = marketCandles.get(interval as CandleInterval);
      if (!candle || candle.openTime.getTime() !== openTime.getTime()) {
        // New candle period, flush old one if exists
        if (candle) {
          await this.flushCandle(trade.marketId, interval as CandleInterval, candle);
        }

        // Create new candle
        candle = {
          openTime,
          closeTime,
          open: trade.price,
          high: trade.price,
          low: trade.price,
          close: trade.price,
          volume: trade.quantity,
          quoteVolume: quoteAmount.toString(),
          trades: 1,
        };
        marketCandles.set(interval as CandleInterval, candle);
      } else {
        // Update existing candle
        candle.high =
          price > parseFloat(candle.high) ? trade.price : candle.high;
        candle.low =
          price < parseFloat(candle.low) ? trade.price : candle.low;
        candle.close = trade.price;
        candle.volume = (parseFloat(candle.volume) + quantity).toString();
        candle.quoteVolume = (
          parseFloat(candle.quoteVolume) + quoteAmount
        ).toString();
        candle.trades++;
      }
    }
  }

  /**
   * Flush all active candles to database
   */
  private async flushCandles(): Promise<void> {
    const now = Date.now();
    const candlesToFlush: Array<{
      marketId: string;
      interval: CandleInterval;
      candle: CandleState;
    }> = [];

    // Find candles that are closed (current time > closeTime)
    for (const [marketId, marketCandles] of this.activeCandlesCache.entries()) {
      for (const [interval, candle] of marketCandles.entries()) {
        if (now >= candle.closeTime.getTime()) {
          candlesToFlush.push({ marketId, interval, candle });
        }
      }
    }

    // Flush to database
    if (candlesToFlush.length > 0) {
      this.logger.debug(
        { count: candlesToFlush.length },
        'Flushing candles to database'
      );

      for (const { marketId, interval, candle } of candlesToFlush) {
        await this.flushCandle(marketId, interval, candle);

        // Remove from cache
        const marketCandles = this.activeCandlesCache.get(marketId);
        if (marketCandles) {
          marketCandles.delete(interval);
          if (marketCandles.size === 0) {
            this.activeCandlesCache.delete(marketId);
          }
        }
      }
    }
  }

  /**
   * Flush a single candle to database
   */
  private async flushCandle(
    marketId: string,
    interval: CandleInterval,
    candle: CandleState
  ): Promise<void> {
    try {
      await this.candlesRepo.upsertCandle(marketId, interval, candle.openTime, {
        closeTime: candle.closeTime,
        open: candle.open,
        high: candle.high,
        low: candle.low,
        close: candle.close,
        volume: candle.volume,
        quoteVolume: candle.quoteVolume,
        trades: candle.trades,
      });
    } catch (error) {
      this.logger.error(
        { error, marketId, interval, openTime: candle.openTime },
        'Failed to flush candle'
      );
    }
  }

  /**
   * Poll recent trades from database (fallback if no event stream)
   */
  private async pollRecentTrades(): Promise<void> {
    // This is a fallback implementation
    // In production, subscribe to trade events from matching engine via NATS
    this.logger.warn(
      'Using database polling for trades - implement NATS subscription for production'
    );

    let lastTradeId: string | undefined;

    const poll = async () => {
      try {
        const where: any = {};
        if (lastTradeId) {
          where.id = { gt: lastTradeId };
        }

        const trades = await this.prisma.trade.findMany({
          where,
          orderBy: { id: 'asc' },
          take: 100,
        });

        for (const trade of trades) {
          await this.processTrade({
            marketId: trade.marketId,
            price: trade.price.toString(),
            quantity: trade.quantity.toString(),
            executedAt: trade.executedAt,
          });
          lastTradeId = trade.id;
        }
      } catch (error) {
        this.logger.error({ error }, 'Failed to poll trades');
      }
    };

    // Poll every 5 seconds
    setInterval(poll, 5000);
  }
}

/**
 * Start candles aggregator as a standalone process
 */
export async function startCandlesAggregator(
  prisma: PrismaClient,
  config: Config,
  logger: Logger
): Promise<CandlesAggregator> {
  const aggregator = new CandlesAggregator(prisma, config, logger);
  await aggregator.start();

  // Graceful shutdown
  const shutdown = async () => {
    await aggregator.stop();
    await prisma.$disconnect();
    process.exit(0);
  };

  process.on('SIGTERM', shutdown);
  process.on('SIGINT', shutdown);

  return aggregator;
}
