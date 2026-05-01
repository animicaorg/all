/**
 * Fee Routes
 * Manage maker/taker fee schedules.
 */

import { Router } from 'express';
import { z } from 'zod';
import type { PrismaClient } from '@prisma/client';
import type { Config } from '../../config.js';
import type { Logger } from '../../utils/logger.js';
import { validateBody, validateParams, validateQuery, commonSchemas } from '../middleware/validation.js';
import { requirePermission, PERMISSIONS } from '../middleware/rbac.js';

const feeQuerySchema = z.object({
  scope: z.enum(['GLOBAL', 'USER_TIER', 'MARKET']).optional(),
  status: z.string().optional(),
  marketId: z.string().uuid().optional(),
  ...commonSchemas.paginationQuery.shape,
});

const feeWriteSchema = z.object({
  scope: z.enum(['GLOBAL', 'USER_TIER', 'MARKET']),
  name: z.string().min(1).max(120).optional().nullable(),
  marketId: z.string().uuid().optional().nullable(),
  userId: z.string().uuid().optional().nullable(),
  makerBps: z.coerce.number().int().min(0).max(10000),
  takerBps: z.coerce.number().int().min(0).max(10000),
  withdrawalFeeOverride: z.string().regex(/^\d+(\.\d+)?$/).optional().nullable(),
  rulesJson: z.any().optional().nullable(),
  status: z.string().min(1).max(40).default('active'),
  effectiveFrom: z.coerce.date(),
  effectiveTo: z.coerce.date().optional().nullable(),
});

const feePatchSchema = feeWriteSchema.partial();

export function createFeesRouter(
  prisma: PrismaClient,
  _config: Config,
  _logger: Logger
): Router {
  const router = Router();

  router.get(
    '/',
    requirePermission(PERMISSIONS.FEES_READ),
    validateQuery(feeQuerySchema),
    async (req, res, next) => {
      try {
        const { scope, status, marketId, page = 1, limit = 50 } = req.query as any;
        const where: any = {};
        if (scope) where.scope = scope;
        if (status) where.status = status;
        if (marketId) where.marketId = marketId;

        const [fees, total, markets] = await Promise.all([
          prisma.feeSchedule.findMany({
            where,
            include: {
              market: {
                select: {
                  id: true,
                  symbol: true,
                },
              },
              creator: {
                select: {
                  id: true,
                  email: true,
                },
              },
            },
            orderBy: [{ status: 'asc' }, { effectiveFrom: 'desc' }],
            skip: (page - 1) * limit,
            take: limit,
          }),
          prisma.feeSchedule.count({ where }),
          prisma.market.findMany({
            select: { id: true, symbol: true },
            orderBy: { symbol: 'asc' },
          }),
        ]);

        res.json({
          success: true,
          data: {
            fees,
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

  router.post(
    '/',
    requirePermission(PERMISSIONS.FEES_WRITE),
    validateBody(feeWriteSchema),
    async (req, res, next) => {
      try {
        if (req.body.scope === 'MARKET' && !req.body.marketId) {
          res.status(400).json({ error: 'ValidationError', message: 'marketId is required for MARKET fees' });
          return;
        }

        const fee = await prisma.feeSchedule.create({
          data: {
            scope: req.body.scope,
            name: req.body.name ?? null,
            marketId: req.body.scope === 'MARKET' ? req.body.marketId : null,
            userId: req.body.scope === 'USER_TIER' ? req.body.userId ?? null : null,
            makerBps: req.body.makerBps,
            takerBps: req.body.takerBps,
            withdrawalFeeOverride: req.body.withdrawalFeeOverride ?? null,
            rulesJson: req.body.rulesJson ?? null,
            status: req.body.status,
            effectiveFrom: req.body.effectiveFrom,
            effectiveTo: req.body.effectiveTo ?? null,
            createdBy: req.admin!.id,
          },
          include: {
            market: { select: { id: true, symbol: true } },
            creator: { select: { id: true, email: true } },
          },
        });

        await req.auditLog?.({
          action: 'CREATE_FEE_SCHEDULE',
          entityType: 'FEE_SCHEDULE',
          entityId: fee.id,
          afterSnapshot: fee,
        });

        res.status(201).json({ success: true, data: { fee } });
      } catch (error) {
        next(error);
      }
    }
  );

  router.patch(
    '/:id',
    requirePermission(PERMISSIONS.FEES_WRITE),
    validateParams(z.object({ id: commonSchemas.uuid })),
    validateBody(feePatchSchema),
    async (req, res, next) => {
      try {
        const existing = await prisma.feeSchedule.findUnique({
          where: { id: req.params.id },
        });
        if (!existing) {
          res.status(404).json({ error: 'NotFound', message: 'Fee schedule not found' });
          return;
        }

        const fee = await prisma.feeSchedule.update({
          where: { id: req.params.id },
          data: {
            scope: req.body.scope,
            name: req.body.name,
            marketId: req.body.marketId,
            userId: req.body.userId,
            makerBps: req.body.makerBps,
            takerBps: req.body.takerBps,
            withdrawalFeeOverride: req.body.withdrawalFeeOverride,
            rulesJson: req.body.rulesJson,
            status: req.body.status,
            effectiveFrom: req.body.effectiveFrom,
            effectiveTo: req.body.effectiveTo,
          },
          include: {
            market: { select: { id: true, symbol: true } },
            creator: { select: { id: true, email: true } },
          },
        });

        await req.auditLog?.({
          action: 'UPDATE_FEE_SCHEDULE',
          entityType: 'FEE_SCHEDULE',
          entityId: fee.id,
          beforeSnapshot: existing,
          afterSnapshot: fee,
        });

        res.json({ success: true, data: { fee } });
      } catch (error) {
        next(error);
      }
    }
  );

  router.delete(
    '/:id',
    requirePermission(PERMISSIONS.FEES_WRITE),
    validateParams(z.object({ id: commonSchemas.uuid })),
    async (req, res, next) => {
      try {
        const existing = await prisma.feeSchedule.findUnique({ where: { id: req.params.id } });
        if (!existing) {
          res.status(404).json({ error: 'NotFound', message: 'Fee schedule not found' });
          return;
        }

        const fee = await prisma.feeSchedule.update({
          where: { id: req.params.id },
          data: {
            status: 'archived',
            effectiveTo: new Date(),
          },
        });

        await req.auditLog?.({
          action: 'UPDATE_FEE_SCHEDULE',
          entityType: 'FEE_SCHEDULE',
          entityId: fee.id,
          beforeSnapshot: existing,
          afterSnapshot: fee,
          metadata: { archived: true },
        });

        res.json({ success: true, data: { fee } });
      } catch (error) {
        next(error);
      }
    }
  );

  return router;
}
