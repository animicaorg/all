/**
 * Users Routes
 * User management endpoints
 */

import { Router } from 'express';
import { z } from 'zod';
import type { PrismaClient } from '@prisma/client';
import type { Config } from '../../config.js';
import type { Logger } from '../../utils/logger.js';
import { validateBody, validateQuery, validateParams, commonSchemas } from '../middleware/validation.js';
import { requirePermission, PERMISSIONS } from '../middleware/rbac.js';

const searchUsersSchema = z.object({
  query: z.string().optional(),
  status: z.enum(['ACTIVE', 'SUSPENDED', 'CLOSED']).optional(),
  ...commonSchemas.paginationQuery.shape,
});

const freezeUserSchema = z.object({
  reason: z.string().min(10).max(500),
});

function isUuid(value: string): boolean {
  return /^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i.test(value);
}

export function createUsersRouter(
  prisma: PrismaClient,
  config: Config,
  logger: Logger
): Router {
  const router = Router();

  /**
   * GET /admin/v1/users
   * Search and list users
   */
  router.get(
    '/',
    requirePermission(PERMISSIONS.USERS_READ),
    validateQuery(searchUsersSchema),
    async (req, res, next) => {
      try {
        const { query, status, page = 1, limit = 50 } = req.query as any;

        const where: any = {};
        if (query) {
          where.OR = [{ email: { contains: query, mode: 'insensitive' } }];
          if (isUuid(query)) {
            where.OR.push({ id: query });
          }
        }
        if (status) {
          where.status = status;
        }

        const [users, total] = await Promise.all([
          prisma.user.findMany({
            where,
            select: {
              id: true,
              email: true,
              status: true,
              role: true,
              twofaEnabled: true,
              createdAt: true,
              updatedAt: true,
            },
            skip: (page - 1) * limit,
            take: limit,
            orderBy: { createdAt: 'desc' },
          }),
          prisma.user.count({ where }),
        ]);

        res.json({
          success: true,
          data: {
            users,
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

  /**
   * GET /admin/v1/users/:id
   * Get user details
   */
  router.get(
    '/:id',
    requirePermission(PERMISSIONS.USERS_READ),
    validateParams(z.object({ id: commonSchemas.uuid })),
    async (req, res, next) => {
      try {
        const user = await prisma.user.findUnique({
          where: { id: req.params.id },
          include: {
            profile: true,
            kycCases: {
              orderBy: { createdAt: 'desc' },
              take: 1,
            },
            riskFlags: {
              where: { status: 'OPEN' },
              orderBy: { createdAt: 'desc' },
            },
          },
        });

        if (!user) {
          res.status(404).json({ error: 'NotFound', message: 'User not found' });
          return;
        }

        // Get balance summary
        const balances = await prisma.ledgerAccount.findMany({
          where: {
            ownerId: user.id,
            accountType: 'AVAILABLE',
          },
          include: {
            asset: true,
            balanceCache: true,
          },
        });

        // Get recent activity
        const recentOrders = await prisma.order.count({
          where: {
            userId: user.id,
            createdAt: { gte: new Date(Date.now() - 30 * 24 * 60 * 60 * 1000) },
          },
        });

        res.json({
          success: true,
          data: {
            user: {
              ...user,
              passwordHash: undefined,
            },
            balances: balances.map((b) => ({
              asset: b.asset.symbol,
              available: b.balanceCache?.available || '0',
              locked: b.balanceCache?.locked || '0',
            })),
            stats: {
              recentOrders,
            },
          },
        });
      } catch (error) {
        next(error);
      }
    }
  );

  /**
   * POST /admin/v1/users/:id/freeze
   * Freeze user account
   */
  router.post(
    '/:id/freeze',
    requirePermission(PERMISSIONS.USERS_FREEZE),
    validateParams(z.object({ id: commonSchemas.uuid })),
    validateBody(freezeUserSchema),
    async (req, res, next) => {
      try {
        const { reason } = req.body;

        const user = await prisma.user.findUnique({
          where: { id: req.params.id },
        });

        if (!user) {
          res.status(404).json({ error: 'NotFound', message: 'User not found' });
          return;
        }

        if (user.status === 'SUSPENDED') {
          res.status(400).json({ error: 'BadRequest', message: 'User already frozen' });
          return;
        }

        // Update user status
        const updated = await prisma.user.update({
          where: { id: req.params.id },
          data: { status: 'SUSPENDED' },
        });

        // Create risk flag
        await prisma.riskFlag.create({
          data: {
            userId: user.id,
            code: 'ACCOUNT_FROZEN',
            severity: 'HIGH',
            note: reason,
            createdBy: req.admin!.id,
          },
        });

        // Log audit event
        await req.auditLog?.({
          action: 'FREEZE_USER',
          entityType: 'USER',
          entityId: user.id,
          beforeSnapshot: { status: user.status },
          afterSnapshot: { status: 'SUSPENDED' },
          metadata: { reason },
        });

        res.json({
          success: true,
          data: { user: updated },
        });
      } catch (error) {
        next(error);
      }
    }
  );

  /**
   * POST /admin/v1/users/:id/unfreeze
   * Unfreeze user account
   */
  router.post(
    '/:id/unfreeze',
    requirePermission(PERMISSIONS.USERS_FREEZE),
    validateParams(z.object({ id: commonSchemas.uuid })),
    async (req, res, next) => {
      try {
        const user = await prisma.user.findUnique({
          where: { id: req.params.id },
        });

        if (!user) {
          res.status(404).json({ error: 'NotFound', message: 'User not found' });
          return;
        }

        if (user.status !== 'SUSPENDED') {
          res.status(400).json({ error: 'BadRequest', message: 'User not frozen' });
          return;
        }

        // Update user status
        const updated = await prisma.user.update({
          where: { id: req.params.id },
          data: { status: 'ACTIVE' },
        });

        // Close related risk flags
        await prisma.riskFlag.updateMany({
          where: {
            userId: user.id,
            code: 'ACCOUNT_FROZEN',
            status: 'OPEN',
          },
          data: {
            status: 'CLOSED',
            closedAt: new Date(),
          },
        });

        // Log audit event
        await req.auditLog?.({
          action: 'UNFREEZE_USER',
          entityType: 'USER',
          entityId: user.id,
          beforeSnapshot: { status: user.status },
          afterSnapshot: { status: 'ACTIVE' },
        });

        res.json({
          success: true,
          data: { user: updated },
        });
      } catch (error) {
        next(error);
      }
    }
  );

  return router;
}
