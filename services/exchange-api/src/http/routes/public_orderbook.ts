/**
 * Public Orderbook Endpoint
 * GET /api/v1/orderbook
 */

import { Router } from 'express';
import { z } from 'zod';
import type { Logger } from '../../utils/logger.js';
import type { Config } from '../../config.js';
import { validate } from '../middleware/validation.js';
import { MarketDataCache } from '../../services/market_data_cache.js';
import { BadRequestError } from '../../utils/errors.js';

const orderbookQuerySchema = z.object({
  market: z.string().min(1, 'Market is required'),
  depth: z
    .string()
    .optional()
    .transform((val) => (val ? parseInt(val, 10) : 50))
    .refine((val) => val > 0 && val <= 500, {
      message: 'Depth must be between 1 and 500',
    }),
});

export interface OrderbookResponse {
  market: string;
  ts: number;
  seq: number;
  bids: Array<[string, string]>; // [price, size]
  asks: Array<[string, string]>;
  checksum?: string;
}

export function createPublicOrderbookRouter(
  cache: MarketDataCache,
  config: Config,
  logger: Logger
): Router {
  const router = Router();

  /**
   * GET /api/v1/orderbook
   * Returns orderbook snapshot for a market
   */
  router.get(
    '/',
    validate({ query: orderbookQuerySchema }),
    async (req, res, next) => {
      try {
        const { market, depth } = (req as any).validated.query;

        const maxDepth = Math.min(depth, config.ORDERBOOK_MAX_DEPTH);
        const snapshot = cache.getSnapshot(market, maxDepth);

        if (!snapshot) {
          throw new BadRequestError(`Market not found: ${market}`);
        }

        const response: OrderbookResponse = {
          market,
          ts: Date.now(),
          seq: snapshot.seq,
          bids: snapshot.bids.map((level) => [
            level.price,
            level.size,
          ]),
          asks: snapshot.asks.map((level) => [
            level.price,
            level.size,
          ]),
        };

        res.json(response);
      } catch (error) {
        logger.error({ error }, 'Failed to fetch orderbook');
        next(error);
      }
    }
  );

  return router;
}
