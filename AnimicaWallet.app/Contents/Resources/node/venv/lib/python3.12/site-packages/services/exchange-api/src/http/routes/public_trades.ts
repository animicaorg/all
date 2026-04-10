/**
 * Public Trades Endpoint
 * GET /api/v1/trades
 */

import { Router } from 'express';
import { z } from 'zod';
import type { PrismaClient } from '@prisma/client';
import type { Logger } from '../../utils/logger.js';
import type { Config } from '../../config.js';
import { validate } from '../middleware/validation.js';
import {
  createPaginationResponse,
  encodeCursor,
  decodeCursor,
} from '../middleware/pagination.js';
import { BadRequestError } from '../../utils/errors.js';

const tradesQuerySchema = (config: Config) =>
  z.object({
    market: z.string().min(1, 'Market is required'),
    limit: z
      .string()
      .optional()
      .transform((val) => (val ? parseInt(val, 10) : 50))
      .refine((val) => val > 0 && val <= config.TRADES_MAX_LIMIT, {
        message: `Limit must be between 1 and ${config.TRADES_MAX_LIMIT}`,
      }),
    cursor: z.string().optional(),
  });

export interface TradeResponse {
  trade_id: string;
  ts: number;
  price: string;
  size: string;
  side: 'buy' | 'sell';
  maker_order_id?: string;
  taker_order_id?: string;
}

export function createPublicTradesRouter(
  prisma: PrismaClient,
  config: Config,
  logger: Logger
): Router {
  const router = Router();

  /**
   * GET /api/v1/trades
   * Returns recent trades for a market with cursor pagination
   */
  router.get(
    '/',
    validate({ query: tradesQuerySchema(config) }),
    async (req, res, next) => {
      try {
        const { market, limit, cursor } = (req as any).validated.query;

        // Get market
        const marketRecord = await prisma.market.findUnique({
          where: { symbol: market },
        });

        if (!marketRecord) {
          throw new BadRequestError(`Market not found: ${market}`);
        }

        // Parse cursor
        let cursorTs: Date | undefined;
        let cursorId: string | undefined;
        if (cursor) {
          try {
            const decoded = decodeCursor(cursor);
            cursorTs = new Date(decoded.ts as string);
            cursorId = decoded.id as string;
          } catch {
            throw new BadRequestError('Invalid cursor format');
          }
        }

        // Build where clause
        const where: any = { marketId: marketRecord.id };
        if (cursorTs && cursorId) {
          where.OR = [
            { executedAt: { lt: cursorTs } },
            { executedAt: cursorTs, id: { lt: cursorId } },
          ];
        }

        // Fetch trades (get limit + 1 to check if there are more)
        const trades = await prisma.trade.findMany({
          where,
          orderBy: [{ executedAt: 'desc' }, { id: 'desc' }],
          take: limit + 1,
          include: {
            makerOrder: { select: { id: true } },
            takerOrder: { select: { id: true } },
          },
        });

        // Map to response format
        const tradeResponses: TradeResponse[] = trades
          .slice(0, limit)
          .map((trade) => ({
            trade_id: trade.id,
            ts: trade.executedAt.getTime(),
            price: trade.price.toString(),
            size: trade.quantity.toString(),
            side: trade.side === 'BUY' ? 'buy' : 'sell',
            maker_order_id: trade.makerOrder?.id,
            taker_order_id: trade.takerOrder?.id,
          }));

        // Create pagination response
        const response = createPaginationResponse(
          trades.slice(0, limit + 1),
          limit,
          (trade) =>
            encodeCursor({
              ts: trade.executedAt.toISOString(),
              id: trade.id,
            })
        );

        res.json({
          data: tradeResponses,
          pagination: response.pagination,
        });
      } catch (error) {
        logger.error({ error }, 'Failed to fetch trades');
        next(error);
      }
    }
  );

  return router;
}
