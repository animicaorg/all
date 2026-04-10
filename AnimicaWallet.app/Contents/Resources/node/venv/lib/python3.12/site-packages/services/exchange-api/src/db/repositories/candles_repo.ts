/**
 * Candles Repository
 * Data access layer for OHLCV candle data
 */

import type { PrismaClient, Candle, CandleInterval } from '@prisma/client';
import { Prisma } from '@prisma/client';

export interface CandleQuery {
  marketId: string;
  interval: CandleInterval;
  startTime?: Date;
  endTime?: Date;
  limit?: number;
}

export class CandlesRepository {
  constructor(private prisma: PrismaClient) {}

  /**
   * Get candles for a market
   */
  async getCandles(query: CandleQuery): Promise<Candle[]> {
    const where: Prisma.CandleWhereInput = {
      marketId: query.marketId,
      interval: query.interval,
    };

    if (query.startTime || query.endTime) {
      where.openTime = {};
      if (query.startTime) {
        where.openTime.gte = query.startTime;
      }
      if (query.endTime) {
        where.openTime.lte = query.endTime;
      }
    }

    return this.prisma.candle.findMany({
      where,
      orderBy: {
        openTime: 'asc',
      },
      take: query.limit,
    });
  }

  /**
   * Upsert a candle (update if exists, insert if not)
   */
  async upsertCandle(
    marketId: string,
    interval: CandleInterval,
    openTime: Date,
    data: {
      closeTime: Date;
      open: string;
      high: string;
      low: string;
      close: string;
      volume: string;
      quoteVolume?: string;
      trades: number;
    }
  ): Promise<Candle> {
    return this.prisma.candle.upsert({
      where: {
        marketId_interval_openTime: {
          marketId,
          interval,
          openTime,
        },
      },
      create: {
        marketId,
        interval,
        openTime,
        ...data,
      },
      update: {
        ...data,
      },
    });
  }

  /**
   * Get latest candle for a market and interval
   */
  async getLatestCandle(
    marketId: string,
    interval: CandleInterval
  ): Promise<Candle | null> {
    return this.prisma.candle.findFirst({
      where: {
        marketId,
        interval,
      },
      orderBy: {
        openTime: 'desc',
      },
    });
  }

  /**
   * Bulk upsert candles (for batch processing)
   */
  async bulkUpsertCandles(
    candles: Array<{
      marketId: string;
      interval: CandleInterval;
      openTime: Date;
      closeTime: Date;
      open: string;
      high: string;
      low: string;
      close: string;
      volume: string;
      quoteVolume?: string;
      trades: number;
    }>
  ): Promise<void> {
    // Use transaction for batch operations
    await this.prisma.$transaction(
      candles.map((candle) =>
        this.prisma.candle.upsert({
          where: {
            marketId_interval_openTime: {
              marketId: candle.marketId,
              interval: candle.interval,
              openTime: candle.openTime,
            },
          },
          create: candle,
          update: {
            closeTime: candle.closeTime,
            open: candle.open,
            high: candle.high,
            low: candle.low,
            close: candle.close,
            volume: candle.volume,
            quoteVolume: candle.quoteVolume,
            trades: candle.trades,
          },
        })
      )
    );
  }
}
