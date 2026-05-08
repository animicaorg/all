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
import { columnExists, countSql, pagination, rowsSql, tableExists } from './db_helpers.js';

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

interface UserRow {
  id: string;
  email: string | null;
  status: string | null;
  role: string | null;
  twofa_enabled: boolean | null;
  created_at: Date;
  updated_at: Date | null;
  balance_totals?: BalanceRow[] | null;
}

interface RiskFlagRow {
  id: string;
  user_id: string;
  code: string;
  severity: string;
  note: string | null;
  status: string;
  created_at: Date;
  closed_at: Date | null;
}

interface BalanceRow {
  asset: string;
  available: string;
  locked: string;
  total: string;
}

function mapUser(row: UserRow) {
  return {
    id: row.id,
    email: row.email,
    status: row.status ?? 'ACTIVE',
    role: row.role ?? 'USER',
    twofaEnabled: row.twofa_enabled ?? false,
    createdAt: row.created_at,
    updatedAt: row.updated_at ?? row.created_at,
    balanceTotals: Array.isArray(row.balance_totals)
      ? row.balance_totals.map((balance) => ({
          asset: balance.asset,
          total: balance.total,
        }))
      : [],
  };
}

function mapRiskFlag(row: RiskFlagRow) {
  return {
    id: row.id,
    userId: row.user_id,
    code: row.code,
    severity: row.severity,
    note: row.note,
    status: row.status,
    createdAt: row.created_at,
    closedAt: row.closed_at,
  };
}

async function userSelectExpressions(prisma: PrismaClient) {
  const [hasTwofaEnabled, hasTwoFaEnabled, hasStatus, hasRole, hasUpdatedAt] = await Promise.all([
    columnExists(prisma, 'users', 'twofa_enabled'),
    columnExists(prisma, 'users', 'two_fa_enabled'),
    columnExists(prisma, 'users', 'status'),
    columnExists(prisma, 'users', 'role'),
    columnExists(prisma, 'users', 'updated_at'),
  ]);

  return {
    status: hasStatus ? 'status::text' : "'ACTIVE'",
    role: hasRole ? 'role::text' : "'USER'",
    twofa: hasTwofaEnabled ? 'twofa_enabled' : hasTwoFaEnabled ? 'two_fa_enabled' : 'false',
    updatedAt: hasUpdatedAt ? 'updated_at' : 'created_at',
    hasStatus,
  };
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
        const expr = await userSelectExpressions(prisma);
        const where: string[] = [];
        const values: unknown[] = [];

        if (query) {
          const conditions = [`email ILIKE $${values.length + 1}`];
          values.push(`%${query}%`);
          if (isUuid(query)) {
            conditions.push(`id = $${values.length + 1}::uuid`);
            values.push(query);
          }
          where.push(`(${conditions.join(' OR ')})`);
        }
        if (status) {
          if (expr.hasStatus) {
            where.push(`status::text = $${values.length + 1}`);
            values.push(status);
          } else if (status !== 'ACTIVE') {
            where.push('false');
          }
        }

        const whereSql = where.length ? `WHERE ${where.join(' AND ')}` : '';
        const total = await countSql(prisma, `SELECT COUNT(*)::bigint AS count FROM users ${whereSql}`, ...values);
        const users = await rowsSql<UserRow>(
          prisma,
          `WITH page_users AS (
            SELECT
              id::text AS id,
              email::text AS email,
              ${expr.status} AS status,
              ${expr.role} AS role,
              ${expr.twofa} AS twofa_enabled,
              created_at,
              ${expr.updatedAt} AS updated_at
            FROM users
            ${whereSql}
            ORDER BY created_at DESC
            OFFSET $${values.length + 1}
            LIMIT $${values.length + 2}
          )
          SELECT
            page_users.*,
            COALESCE(
              (
                SELECT jsonb_agg(
                  jsonb_build_object(
                    'asset', balances.asset,
                    'available', balances.available::text,
                    'locked', balances.locked::text,
                    'total', (balances.available + balances.locked)::text
                  )
                  ORDER BY balances.asset
                )
                FROM balances
                WHERE balances.account_id = 'user:' || page_users.id
                  AND (balances.available <> 0 OR balances.locked <> 0)
              ),
              '[]'::jsonb
            ) AS balance_totals
          FROM page_users`,
          ...values,
          (page - 1) * limit,
          limit
        );

        res.json({
          success: true,
          data: {
            users: users.map(mapUser),
            pagination: pagination(page, limit, total),
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
        const expr = await userSelectExpressions(prisma);
        const [user] = await rowsSql<UserRow>(
          prisma,
          `SELECT
            id::text AS id,
            email::text AS email,
            ${expr.status} AS status,
            ${expr.role} AS role,
            ${expr.twofa} AS twofa_enabled,
            created_at,
            ${expr.updatedAt} AS updated_at
          FROM users
          WHERE id = $1::uuid
          LIMIT 1`,
          req.params.id
        );

        if (!user) {
          res.status(404).json({ error: 'NotFound', message: 'User not found' });
          return;
        }

        const riskFlagsExist = await tableExists(prisma, 'risk_flags');
        const [riskFlags, recentOrders, balances] = await Promise.all([
          riskFlagsExist
            ? rowsSql<RiskFlagRow>(
                prisma,
                `SELECT
                  id::text AS id,
                  user_id::text AS user_id,
                  code,
                  severity::text AS severity,
                  note,
                  status::text AS status,
                  created_at,
                  closed_at
                FROM risk_flags
                WHERE user_id = $1::uuid AND status::text = 'OPEN'
                ORDER BY created_at DESC`,
                req.params.id
              )
            : Promise.resolve([]),
          countSql(
            prisma,
            "SELECT COUNT(*)::bigint AS count FROM orders WHERE user_id = $1::uuid AND created_at >= NOW() - interval '30 days'",
            req.params.id
          ),
          rowsSql<BalanceRow>(
            prisma,
            `SELECT
              asset,
              available::text AS available,
              locked::text AS locked,
              (available + locked)::text AS total
            FROM balances
            WHERE account_id = 'user:' || $1::text
              AND (available <> 0 OR locked <> 0)
            ORDER BY asset`,
            req.params.id
          ),
        ]);

        res.json({
          success: true,
          data: {
            user: {
              ...mapUser(user),
              profile: null,
              kycCases: [],
              riskFlags: riskFlags.map(mapRiskFlag),
            },
            balances,
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
