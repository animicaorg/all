/**
 * Public Candles Endpoint
 * GET /api/v1/candles
 */

import { Router } from 'express';
import { z } from 'zod';
import type { PrismaClient, CandleInterval } from '@prisma/client';
import type { Logger } from '../../utils/logger.js';
import type { Config } from '../../config.js';
import { validate } from '../middleware/validation.js';
import { CandlesRepository } from '../../db/repositories/candles_repo.js';
import { MarketsRepository } from '../../db/repositories/markets_repo.js';
import { BadRequestError } from '../../utils/errors.js';

const intervalMap: Record<string, CandleInterval> = {
  '1m': 'ONE_MINUTE',
  '5m': 'FIVE_MINUTES',
  '15m': 'FIFTEEN_MINUTES',
  '1h': 'ONE_HOUR',
  '4h': 'FOUR_HOURS',
  '1d': 'ONE_DAY',
};

const candlesQuerySchema = (config: Config) =>
  z.object({
    market: z.string().min(1, 'Market is required'),
    interval: z
      .string()
      .refine((val) => val in intervalMap, {
        message: 'Invalid interval. Use: 1m, 5m, 15m, 1h, 4h, 1d',
      }),
    start: z
      .string()
      .optional()
      .transform((val) => (val ? new Date(parseInt(val)) : undefined)),
    end: z
      .string()
      .optional()
      .transform((val) => (val ? new Date(parseInt(val)) : undefined)),
    limit: z
      .string()
      .optional()
      .transform((val) => (val ? parseInt(val, 10) : 100))
      .refine((val) => val > 0 && val <= 1000, {
        message: 'Limit must be between 1 and 1000',
      }),
  });

export interface CandleResponse {
  ts: number;
  open: string;
  high: string;
  low: string;
  close: string;
  volume: string;
  quote_volume?: string;
  trades: number;
}

export function createPublicCandlesRouter(
  prisma: PrismaClient,
  config: Config,
  logger: Logger
): Router {
  const router = Router();
  const candlesRepo = new CandlesRepository(prisma);
  const marketsRepo = new MarketsRepository(prisma);

  /**
   * GET /api/v1/candles
   * Returns OHLCV candle data for a market
   */
  router.get(
    '/',
    validate({ query: candlesQuerySchema(config) }),
    async (req, res, next) => {
      try {
        const { market, interval, start, end, limit } = (req as any).validated
          .query;

        // Get market
        const marketRecord = await marketsRepo.getMarketBySymbol(market);
        if (!marketRecord) {
          throw new BadRequestError(`Market not found: ${market}`);
        }

        // Get candles
        const candles = await candlesRepo.getCandles({
          marketId: marketRecord.id,
          interval: intervalMap[interval],
          startTime: start,
          endTime: end,
          limit,
        });

        const response: CandleResponse[] = candles.map((candle) => ({
          ts: candle.openTime.getTime(),
          open: candle.open,
          high: candle.high,
          low: candle.low,
          close: candle.close,
          volume: candle.volume,
          quote_volume: candle.quoteVolume || undefined,
          trades: candle.trades,
        }));

        res.json(response);
      } catch (error) {
        logger.error({ error }, 'Failed to fetch candles');
        next(error);
      }
    }
  );

  return router;
}
