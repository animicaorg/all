/**
 * Public Markets Endpoint
 * GET /api/v1/markets
 */

import { Router } from 'express';
import type { PrismaClient } from '@prisma/client';
import type { Logger } from '../../utils/logger.js';
import { MarketsRepository } from '../../db/repositories/markets_repo.js';

export interface MarketResponse {
  market: string;
  base: string;
  quote: string;
  price_decimals: number;
  size_decimals: number;
  min_order_size: string;
  status: string;
}

export function createPublicMarketsRouter(
  prisma: PrismaClient,
  logger: Logger
): Router {
  const router = Router();
  const marketsRepo = new MarketsRepository(prisma);

  /**
   * GET /api/v1/markets
   * Returns list of all active markets
   */
  router.get('/', async (req, res, next) => {
    try {
      const markets = await marketsRepo.getActiveMarkets();

      const response: MarketResponse[] = markets.map((m) => ({
        market: m.symbol,
        base: (m.baseAsset as any).symbol,
        quote: (m.quoteAsset as any).symbol,
        price_decimals: getPrecision(m.priceTick.toString()),
        size_decimals: getPrecision(m.sizeStep.toString()),
        min_order_size: m.minOrderSize.toString(),
        status: m.status,
      }));

      res.json(response);
    } catch (error) {
      logger.error({ error }, 'Failed to fetch markets');
      next(error);
    }
  });

  return router;
}

/**
 * Calculate decimal precision from tick size
 * e.g., "0.01" -> 2, "0.001" -> 3
 */
function getPrecision(tickSize: string): number {
  const parts = tickSize.split('.');
  return parts.length > 1 ? parts[1].replace(/0+$/, '').length : 0;
}
