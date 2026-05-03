/**
 * Overview Routes
 * Aggregates admin dashboard metrics from live exchange tables.
 */

import { Router } from 'express';
import type { PrismaClient } from '@prisma/client';
import type { Config } from '../../config.js';
import type { Logger } from '../../utils/logger.js';
import { requirePermission, PERMISSIONS } from '../middleware/rbac.js';
import { columnExists, countSql, rowsSql, tableExists, toInt } from './db_helpers.js';

function since(days: number): Date {
  return new Date(Date.now() - days * 24 * 60 * 60 * 1000);
}

interface AuditRow {
  id: string;
  actor_type: string;
  actor: string | null;
  action: string;
  entity_type: string;
  entity_id: string | null;
  created_at: Date;
}

interface WithdrawalTotalRow {
  status: string;
  count: bigint | number | string;
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
        const dayAgoIso = dayAgo.toISOString();
        const monthAgoIso = monthAgo.toISOString();

        const [
          usersExist,
          kycCasesExist,
          withdrawalsExist,
          incidentsExist,
          marketsExist,
          tradesExist,
          auditLogsExist,
          marketsHaveStatus,
          marketsHaveActive,
        ] = await Promise.all([
          tableExists(prisma, 'users'),
          tableExists(prisma, 'kyc_cases'),
          tableExists(prisma, 'withdrawals'),
          tableExists(prisma, 'incidents'),
          tableExists(prisma, 'markets'),
          tableExists(prisma, 'trades'),
          tableExists(prisma, 'audit_logs'),
          columnExists(prisma, 'markets', 'status'),
          columnExists(prisma, 'markets', 'active'),
        ]);

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
          usersExist ? countSql(prisma, 'SELECT COUNT(*)::bigint AS count FROM users') : Promise.resolve(0),
          usersExist ? countSql(prisma, "SELECT COUNT(*)::bigint AS count FROM users WHERE status::text = 'ACTIVE'") : Promise.resolve(0),
          usersExist ? countSql(prisma, `SELECT COUNT(*)::bigint AS count FROM users WHERE created_at >= '${dayAgoIso}'::timestamptz`) : Promise.resolve(0),
          kycCasesExist
            ? countSql(prisma, "SELECT COUNT(*)::bigint AS count FROM kyc_cases WHERE status::text IN ('PENDING', 'REVIEW')")
            : Promise.resolve(0),
          withdrawalsExist
            ? countSql(prisma, "SELECT COUNT(*)::bigint AS count FROM withdrawals WHERE status::text IN ('REQUESTED', 'RISK_REVIEW')")
            : Promise.resolve(0),
          incidentsExist
            ? countSql(prisma, "SELECT COUNT(*)::bigint AS count FROM incidents WHERE status::text IN ('OPEN', 'IN_PROGRESS')")
            : Promise.resolve(0),
          marketsExist && marketsHaveStatus
            ? countSql(prisma, "SELECT COUNT(*)::bigint AS count FROM markets WHERE status::text <> 'ONLINE'")
            : marketsExist && marketsHaveActive
              ? countSql(prisma, 'SELECT COUNT(*)::bigint AS count FROM markets WHERE active = false')
              : Promise.resolve(0),
          marketsExist ? countSql(prisma, 'SELECT COUNT(*)::bigint AS count FROM markets') : Promise.resolve(0),
          tradesExist
            ? countSql(prisma, `SELECT COUNT(*)::bigint AS count FROM trades WHERE created_at >= '${dayAgoIso}'::timestamptz`)
            : Promise.resolve(0),
          auditLogsExist
            ? rowsSql<AuditRow>(
                prisma,
                `SELECT
              audit_logs.id::text AS id,
              audit_logs.actor_type::text AS actor_type,
              COALESCE(admins.email::text, users.email::text, 'system') AS actor,
              COALESCE(audit_logs.action, audit_logs.event_type, 'legacy') AS action,
              COALESCE(audit_logs.entity_type, audit_logs.resource_type, 'UNKNOWN') AS entity_type,
              COALESCE(audit_logs.entity_id, audit_logs.resource_id, '') AS entity_id,
              audit_logs.created_at AS created_at
            FROM audit_logs
            LEFT JOIN admins ON admins.id = audit_logs.actor_admin_id
            LEFT JOIN users ON users.id = COALESCE(audit_logs.actor_user_id, audit_logs.user_id)
            ORDER BY audit_logs.created_at DESC
            LIMIT 8`
              )
            : Promise.resolve([]),
          withdrawalsExist
            ? rowsSql<WithdrawalTotalRow>(
                prisma,
                `SELECT status::text AS status, COUNT(*)::bigint AS count
            FROM withdrawals
            WHERE requested_at >= '${monthAgoIso}'::timestamptz
            GROUP BY status::text`
              )
            : Promise.resolve([]),
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
                  count: toInt(row.count),
                })),
              },
              incidents: { open: openIncidents },
              markets: { total: marketCount, halted: haltedMarkets },
              trades: { last24h: tradeCount24h },
            },
            recentAudit: recentAudit.map((entry) => ({
              id: entry.id,
              actorType: entry.actor_type,
              actor: entry.actor ?? 'system',
              action: entry.action,
              entityType: entry.entity_type,
              entityId: entry.entity_id,
              createdAt: entry.created_at,
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
