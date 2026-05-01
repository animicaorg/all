/**
 * Overview Routes
 * Aggregates admin dashboard metrics from live exchange tables.
 */

import { Router } from 'express';
import type { PrismaClient } from '@prisma/client';
import type { Config } from '../../config.js';
import type { Logger } from '../../utils/logger.js';
import { requirePermission, PERMISSIONS } from '../middleware/rbac.js';

function since(days: number): Date {
  return new Date(Date.now() - days * 24 * 60 * 60 * 1000);
}

export function createOverviewRouter(
  prisma: PrismaClient,
  _config: Config,
  _logger: Logger
): Router {
  const router = Router();

  router.get(
    '/',
    requirePermission(PERMISSIONS.AUDIT_READ),
    async (_req, res, next) => {
      try {
        const dayAgo = since(1);
        const monthAgo = since(30);

        const [
          totalUsers,
          activeUsers,
          newUsers24h,
          pendingKyc,
          openWithdrawals,
          openIncidents,
          haltedMarkets,
          marketCount,
          tradeCount24h,
          recentAudit,
          withdrawalTotals,
        ] = await Promise.all([
          prisma.user.count(),
          prisma.user.count({ where: { status: 'ACTIVE' } }),
          prisma.user.count({ where: { createdAt: { gte: dayAgo } } }),
          prisma.kycCase.count({ where: { status: { in: ['PENDING', 'REVIEW'] } } }),
          prisma.withdrawal.count({ where: { status: { in: ['REQUESTED', 'RISK_REVIEW'] } } }),
          prisma.incident.count({ where: { status: { in: ['OPEN', 'IN_PROGRESS'] } } }),
          prisma.market.count({ where: { status: { not: 'ONLINE' } } }),
          prisma.market.count(),
          prisma.trade.count({ where: { createdAt: { gte: dayAgo } } }),
          prisma.auditLog.findMany({
            include: {
              actorAdmin: { select: { email: true } },
              actor: { select: { email: true } },
            },
            orderBy: { createdAt: 'desc' },
            take: 8,
          }),
          prisma.withdrawal.groupBy({
            by: ['status'],
            where: { requestedAt: { gte: monthAgo } },
            _count: { _all: true },
          }),
        ]);

        res.json({
          success: true,
          data: {
            metrics: {
              users: { total: totalUsers, active: activeUsers, new24h: newUsers24h },
              kyc: { pending: pendingKyc },
              withdrawals: {
                pending: openWithdrawals,
                last30dByStatus: withdrawalTotals.map((row) => ({
                  status: row.status,
                  count: row._count._all,
                })),
              },
              incidents: { open: openIncidents },
              markets: { total: marketCount, halted: haltedMarkets },
              trades: { last24h: tradeCount24h },
            },
            recentAudit: recentAudit.map((entry) => ({
              id: entry.id,
              actorType: entry.actorType,
              actor: entry.actorAdmin?.email ?? entry.actor?.email ?? 'system',
              action: entry.action,
              entityType: entry.entityType,
              entityId: entry.entityId,
              createdAt: entry.createdAt,
            })),
          },
        });
      } catch (error) {
        next(error);
      }
    }
  );

  return router;
}
