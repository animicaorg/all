/**
 * Market Routes
 * Exchange market state and operational controls.
 */

import { Router } from 'express';
import { z } from 'zod';
import type { PrismaClient } from '@prisma/client';
import type { Config } from '../../config.js';
import type { Logger } from '../../utils/logger.js';
import { validateBody, validateParams, validateQuery, commonSchemas } from '../middleware/validation.js';
import { requirePermission, PERMISSIONS } from '../middleware/rbac.js';

const marketsQuerySchema = z.object({
  query: z.string().optional(),
  status: z.enum(['ONLINE', 'HALTED', 'READONLY']).optional(),
  ...commonSchemas.paginationQuery.shape,
});

const marketStatusSchema = z.object({
  status: z.enum(['ONLINE', 'HALTED', 'READONLY']),
  reason: z.string().min(3).max(1000).optional(),
});

const controlsSchema = z.object({
  tradingEnabled: z.boolean(),
  depositsEnabled: z.boolean(),
  withdrawalsEnabled: z.boolean(),
  reason: z.string().max(1000).optional().nullable(),
});

export function createMarketsRouter(
  prisma: PrismaClient,
  _config: Config,
  _logger: Logger
): Router {
  const router = Router();

  router.get(
    '/',
    requirePermission(PERMISSIONS.MARKETS_READ),
    validateQuery(marketsQuerySchema),
    async (req, res, next) => {
      try {
        const { query, status, page = 1, limit = 50 } = req.query as any;
        const where: any = {};
        if (query) {
          where.symbol = { contains: query, mode: 'insensitive' };
        }
        if (status) {
          where.status = status;
        }

        const [markets, total] = await Promise.all([
          prisma.market.findMany({
            where,
            include: {
              baseAsset: true,
              quoteAsset: true,
              marketControl: true,
              _count: {
                select: {
                  orders: true,
                  trades: true,
                },
              },
            },
            orderBy: { symbol: 'asc' },
            skip: (page - 1) * limit,
            take: limit,
          }),
          prisma.market.count({ where }),
        ]);

        res.json({
          success: true,
          data: {
            markets,
            pagination: {
              page,
              limit,
              total,
              totalPages: Math.ceil(total / limit),
            },
          },
        });
      } catch (error) {
        next(error);
      }
    }
  );

  router.patch(
    '/:id/status',
    requirePermission(PERMISSIONS.MARKETS_HALT),
    validateParams(z.object({ id: commonSchemas.uuid })),
    validateBody(marketStatusSchema),
    async (req, res, next) => {
      try {
        const existing = await prisma.market.findUnique({
          where: { id: req.params.id },
          include: { marketControl: true },
        });

        if (!existing) {
          res.status(404).json({ error: 'NotFound', message: 'Market not found' });
          return;
        }

        const updated = await prisma.$transaction(async (tx) => {
          const market = await tx.market.update({
            where: { id: req.params.id },
            data: { status: req.body.status },
            include: {
              baseAsset: true,
              quoteAsset: true,
              marketControl: true,
            },
          });

          await tx.marketControl.upsert({
            where: { marketId: req.params.id },
            create: {
              marketId: req.params.id,
              tradingEnabled: req.body.status === 'ONLINE',
              depositsEnabled: true,
              withdrawalsEnabled: true,
              reason: req.body.reason ?? null,
              updatedBy: req.admin!.id,
            },
            update: {
              tradingEnabled: req.body.status === 'ONLINE',
              reason: req.body.reason ?? null,
              updatedBy: req.admin!.id,
            },
          });

          return market;
        });

        await req.auditLog?.({
          action: req.body.status === 'ONLINE' ? 'RESUME_MARKET' : 'HALT_MARKET',
          entityType: 'MARKET',
          entityId: updated.id,
          beforeSnapshot: { status: existing.status, control: existing.marketControl },
          afterSnapshot: { status: req.body.status },
          metadata: { reason: req.body.reason },
        });

        res.json({ success: true, data: { market: updated } });
      } catch (error) {
        next(error);
      }
    }
  );

  router.put(
    '/:id/controls',
    requirePermission(PERMISSIONS.MARKETS_WRITE),
    validateParams(z.object({ id: commonSchemas.uuid })),
    validateBody(controlsSchema),
    async (req, res, next) => {
      try {
        const market = await prisma.market.findUnique({
          where: { id: req.params.id },
          include: { marketControl: true },
        });

        if (!market) {
          res.status(404).json({ error: 'NotFound', message: 'Market not found' });
          return;
        }

        const control = await prisma.marketControl.upsert({
          where: { marketId: req.params.id },
          create: {
            marketId: req.params.id,
            tradingEnabled: req.body.tradingEnabled,
            depositsEnabled: req.body.depositsEnabled,
            withdrawalsEnabled: req.body.withdrawalsEnabled,
            reason: req.body.reason ?? null,
            updatedBy: req.admin!.id,
          },
          update: {
            tradingEnabled: req.body.tradingEnabled,
            depositsEnabled: req.body.depositsEnabled,
            withdrawalsEnabled: req.body.withdrawalsEnabled,
            reason: req.body.reason ?? null,
            updatedBy: req.admin!.id,
          },
        });

        await req.auditLog?.({
          action: 'UPDATE_MARKET_CONTROLS',
          entityType: 'MARKET',
          entityId: req.params.id,
          beforeSnapshot: market.marketControl,
          afterSnapshot: control,
        });

        res.json({ success: true, data: { control } });
      } catch (error) {
        next(error);
      }
    }
  );

  router.post(
    '/:id/cancel-open-orders',
    requirePermission(PERMISSIONS.MARKETS_HALT),
    validateParams(z.object({ id: commonSchemas.uuid })),
    async (req, res, next) => {
      try {
        const market = await prisma.market.findUnique({ where: { id: req.params.id } });
        if (!market) {
          res.status(404).json({ error: 'NotFound', message: 'Market not found' });
          return;
        }

        const result = await prisma.order.updateMany({
          where: {
            marketId: req.params.id,
            status: { in: ['OPEN', 'PARTIALLY_FILLED'] },
          },
          data: {
            status: 'CANCELED',
            canceledAt: new Date(),
          },
        });

        await req.auditLog?.({
          action: 'CANCEL_ALL_ORDERS',
          entityType: 'MARKET',
          entityId: req.params.id,
          metadata: { canceledOrders: result.count },
        });

        res.json({ success: true, data: { canceledOrders: result.count } });
      } catch (error) {
        next(error);
      }
    }
  );

  return router;
}
