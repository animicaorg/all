/**
 * Withdrawal Routes
 * Queue review and approval actions.
 */

import { Router } from 'express';
import { z } from 'zod';
import type { PrismaClient } from '@prisma/client';
import type { Config } from '../../config.js';
import type { Logger } from '../../utils/logger.js';
import { validateBody, validateParams, validateQuery, commonSchemas } from '../middleware/validation.js';
import { requirePermission, PERMISSIONS } from '../middleware/rbac.js';
import { countSql, pagination, rowsSql, tableExists, toInt } from './db_helpers.js';

const withdrawalQuerySchema = z.object({
  query: z.string().optional(),
  status: z
    .enum(['REQUESTED', 'RISK_REVIEW', 'APPROVED', 'SIGNING', 'BROADCAST', 'CONFIRMED', 'FAILED', 'CANCELED'])
    .optional(),
  provider: z.enum(['BITGO', 'ANIMICA_NODE', 'BITCOIN_NODE', 'MANUAL']).optional(),
  ...commonSchemas.paginationQuery.shape,
});

const noteSchema = z.object({
  note: z.string().max(1000).optional(),
});

function isUuid(value: string): boolean {
  return /^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i.test(value);
}

interface WithdrawalRow {
  id: string;
  user_id: string;
  asset_network_id: string;
  destination_address: string;
  destination_tag: string | null;
  amount: string;
  fee_amount: string | null;
  status: string;
  requested_at: Date;
  approved_at: Date | null;
  broadcast_at: Date | null;
  confirmed_at: Date | null;
  txid: string | null;
  provider: string | null;
  provider_ref: string | null;
  idempotency_key: string | null;
  risk_score: string | null;
  created_at: Date;
  updated_at: Date;
  user_email: string | null;
  user_status: string | null;
  asset_id: string | null;
  asset_symbol: string | null;
  asset_name: string | null;
  asset_decimals: number | null;
  asset_active: boolean | null;
  asset_created_at: Date | null;
  network_id: string | null;
  network_code: string | null;
  network_name: string | null;
  network_kind: string | null;
  confirmations_required: number | null;
  network_created_at: Date | null;
  contract_address: string | null;
  deposit_enabled: boolean | null;
  withdrawal_enabled: boolean | null;
  min_withdrawal: string | null;
}

interface StatusCountRow {
  status: string;
  count: bigint | number | string;
}

function mapWithdrawal(row: WithdrawalRow) {
  const assetCreatedAt = row.asset_created_at ?? row.created_at;
  const networkCreatedAt = row.network_created_at ?? row.created_at;

  return {
    id: row.id,
    userId: row.user_id,
    assetNetworkId: row.asset_network_id,
    destinationAddress: row.destination_address,
    destinationTag: row.destination_tag,
    amount: row.amount,
    feeAmount: row.fee_amount ?? '0',
    status: row.status,
    requestedAt: row.requested_at,
    approvedAt: row.approved_at,
    broadcastAt: row.broadcast_at,
    confirmedAt: row.confirmed_at,
    txid: row.txid,
    provider: row.provider ?? 'MANUAL',
    providerRef: row.provider_ref,
    idempotencyKey: row.idempotency_key,
    riskScore: row.risk_score,
    createdAt: row.created_at,
    updatedAt: row.updated_at,
    user: {
      id: row.user_id,
      email: row.user_email,
      status: row.user_status ?? 'ACTIVE',
    },
    assetNetwork: {
      id: row.asset_network_id,
      assetId: row.asset_id ?? '',
      networkId: row.network_id ?? '',
      contractAddress: row.contract_address,
      depositEnabled: row.deposit_enabled ?? true,
      withdrawalEnabled: row.withdrawal_enabled ?? true,
      minWithdrawal: row.min_withdrawal ?? '0',
      withdrawalFee: row.fee_amount ?? '0',
      asset: {
        id: row.asset_id ?? '',
        symbol: row.asset_symbol ?? 'UNKNOWN',
        name: row.asset_name ?? row.asset_symbol ?? 'UNKNOWN',
        decimals: row.asset_decimals ?? 0,
        kind: 'CRYPTO',
        isEnabled: row.asset_active ?? true,
        createdAt: assetCreatedAt,
      },
      network: {
        id: row.network_id ?? '',
        code: row.network_code ?? 'UNKNOWN',
        kind: row.network_kind ?? row.network_name ?? row.network_code ?? 'UNKNOWN',
        chainId: null,
        rpcUrl: null,
        confirmationsRequired: row.confirmations_required ?? 0,
        createdAt: networkCreatedAt,
      },
    },
    approvals: [],
  };
}

export function createWithdrawalsRouter(
  prisma: PrismaClient,
  _config: Config,
  _logger: Logger
): Router {
  const router = Router();

  router.get(
    '/',
    requirePermission(PERMISSIONS.WITHDRAWALS_READ),
    validateQuery(withdrawalQuerySchema),
    async (req, res, next) => {
      try {
        const { query, status, provider, page = 1, limit = 50 } = req.query as any;
        if (!(await tableExists(prisma, 'withdrawals'))) {
          res.json({
            success: true,
            data: {
              withdrawals: [],
              statusCounts: [],
              pagination: pagination(page, limit, 0),
            },
          });
          return;
        }

        const where: string[] = [];
        const values: unknown[] = [];
        if (status) {
          where.push(`withdrawals.status = $${values.length + 1}`);
          values.push(status);
        }
        if (provider) {
          where.push(`withdrawals.provider = $${values.length + 1}`);
          values.push(provider);
        }
        if (query) {
          const conditions = [
            `withdrawals.txid ILIKE $${values.length + 1}`,
            `withdrawals.destination_address ILIKE $${values.length + 1}`,
            `users.email ILIKE $${values.length + 1}`,
          ];
          values.push(`%${query}%`);
          if (isUuid(query)) {
            conditions.push(`withdrawals.id = $${values.length + 1}::uuid`);
            values.push(query);
            conditions.push(`withdrawals.user_id = $${values.length + 1}::uuid`);
            values.push(query);
          }
          where.push(`(${conditions.join(' OR ')})`);
        }

        const fromSql = `
          FROM withdrawals
          LEFT JOIN users ON users.id = withdrawals.user_id
          LEFT JOIN asset_networks ON asset_networks.id = withdrawals.asset_network_id
          LEFT JOIN assets ON assets.id = asset_networks.asset_id
          LEFT JOIN networks ON networks.id = asset_networks.network_id
        `;
        const whereSql = where.length ? `WHERE ${where.join(' AND ')}` : '';
        const [withdrawals, total, statusCounts] = await Promise.all([
          rowsSql<WithdrawalRow>(
            prisma,
            `SELECT
              withdrawals.id::text AS id,
              withdrawals.user_id::text AS user_id,
              withdrawals.asset_network_id::text AS asset_network_id,
              withdrawals.destination_address::text AS destination_address,
              withdrawals.destination_tag::text AS destination_tag,
              withdrawals.amount::text AS amount,
              withdrawals.fee_amount::text AS fee_amount,
              withdrawals.status::text AS status,
              withdrawals.requested_at,
              withdrawals.approved_at,
              withdrawals.broadcast_at,
              withdrawals.confirmed_at,
              withdrawals.txid::text AS txid,
              withdrawals.provider::text AS provider,
              withdrawals.provider_ref::text AS provider_ref,
              withdrawals.idempotency_key::text AS idempotency_key,
              withdrawals.risk_score::text AS risk_score,
              withdrawals.created_at,
              withdrawals.updated_at,
              users.email::text AS user_email,
              users.status::text AS user_status,
              assets.id::text AS asset_id,
              assets.symbol::text AS asset_symbol,
              assets.name::text AS asset_name,
              assets.decimals AS asset_decimals,
              assets.active AS asset_active,
              assets.created_at AS asset_created_at,
              networks.id::text AS network_id,
              networks.code::text AS network_code,
              networks.name::text AS network_name,
              networks.type::text AS network_kind,
              networks.confirmations_required,
              networks.created_at AS network_created_at,
              asset_networks.contract_address::text AS contract_address,
              asset_networks.deposits_enabled AS deposit_enabled,
              asset_networks.withdrawals_enabled AS withdrawal_enabled,
              asset_networks.min_deposit_atoms::text AS min_withdrawal
            ${fromSql}
            ${whereSql}
            ORDER BY withdrawals.requested_at DESC
            OFFSET $${values.length + 1}
            LIMIT $${values.length + 2}`,
            ...values,
            (page - 1) * limit,
            limit
          ),
          countSql(prisma, `SELECT COUNT(*)::bigint AS count ${fromSql} ${whereSql}`, ...values),
          rowsSql<StatusCountRow>(
            prisma,
            'SELECT status::text AS status, COUNT(*)::bigint AS count FROM withdrawals GROUP BY status::text'
          ),
        ]);

        res.json({
          success: true,
          data: {
            withdrawals: withdrawals.map(mapWithdrawal),
            statusCounts: statusCounts.map((row: StatusCountRow) => ({
              status: row.status,
              count: toInt(row.count),
            })),
            pagination: pagination(page, limit, total),
          },
        });
      } catch (error) {
        next(error);
      }
    }
  );

  router.post(
    '/:id/approve',
    requirePermission(PERMISSIONS.WITHDRAWALS_APPROVE),
    validateParams(z.object({ id: commonSchemas.uuid })),
    validateBody(noteSchema),
    async (req, res, next) => {
      try {
        const existing = await prisma.withdrawal.findUnique({
          where: { id: req.params.id },
          include: { approvals: true },
        });
        if (!existing) {
          res.status(404).json({ error: 'NotFound', message: 'Withdrawal not found' });
          return;
        }
        if (!['REQUESTED', 'RISK_REVIEW'].includes(existing.status)) {
          res.status(400).json({
            error: 'BadRequest',
            message: `Withdrawal cannot be approved from ${existing.status}`,
          });
          return;
        }

        const withdrawal = await prisma.$transaction(async (tx) => {
          await tx.withdrawalApproval.create({
            data: {
              withdrawalId: req.params.id,
              approverAdminId: req.admin!.id,
              action: 'APPROVE',
              note: req.body.note,
            },
          });

          return tx.withdrawal.update({
            where: { id: req.params.id },
            data: {
              status: 'APPROVED',
              approvedAt: new Date(),
            },
            include: {
              user: { select: { id: true, email: true, status: true } },
              assetNetwork: { include: { asset: true, network: true } },
              approvals: {
                include: {
                  approverAdmin: { select: { id: true, email: true, role: true } },
                  approverUser: { select: { id: true, email: true } },
                },
              },
            },
          });
        });

        await req.auditLog?.({
          action: 'APPROVE_WITHDRAWAL',
          entityType: 'WITHDRAWAL',
          entityId: withdrawal.id,
          beforeSnapshot: existing,
          afterSnapshot: withdrawal,
          metadata: { note: req.body.note },
        });

        res.json({ success: true, data: { withdrawal } });
      } catch (error) {
        next(error);
      }
    }
  );

  router.post(
    '/:id/reject',
    requirePermission(PERMISSIONS.WITHDRAWALS_APPROVE),
    validateParams(z.object({ id: commonSchemas.uuid })),
    validateBody(noteSchema.extend({ note: z.string().min(3).max(1000) })),
    async (req, res, next) => {
      try {
        const existing = await prisma.withdrawal.findUnique({
          where: { id: req.params.id },
          include: { approvals: true },
        });
        if (!existing) {
          res.status(404).json({ error: 'NotFound', message: 'Withdrawal not found' });
          return;
        }
        if (!['REQUESTED', 'RISK_REVIEW', 'APPROVED'].includes(existing.status)) {
          res.status(400).json({
            error: 'BadRequest',
            message: `Withdrawal cannot be rejected from ${existing.status}`,
          });
          return;
        }

        const withdrawal = await prisma.$transaction(async (tx) => {
          await tx.withdrawalApproval.create({
            data: {
              withdrawalId: req.params.id,
              approverAdminId: req.admin!.id,
              action: 'REJECT',
              note: req.body.note,
            },
          });

          return tx.withdrawal.update({
            where: { id: req.params.id },
            data: {
              status: 'CANCELED',
            },
            include: {
              user: { select: { id: true, email: true, status: true } },
              assetNetwork: { include: { asset: true, network: true } },
              approvals: {
                include: {
                  approverAdmin: { select: { id: true, email: true, role: true } },
                  approverUser: { select: { id: true, email: true } },
                },
              },
            },
          });
        });

        await req.auditLog?.({
          action: 'DENY_WITHDRAWAL',
          entityType: 'WITHDRAWAL',
          entityId: withdrawal.id,
          beforeSnapshot: existing,
          afterSnapshot: withdrawal,
          metadata: { note: req.body.note },
        });

        res.json({ success: true, data: { withdrawal } });
      } catch (error) {
        next(error);
      }
    }
  );

  router.post(
    '/:id/retry',
    requirePermission(PERMISSIONS.WITHDRAWALS_SIGN),
    validateParams(z.object({ id: commonSchemas.uuid })),
    validateBody(noteSchema),
    async (req, res, next) => {
      try {
        const existing = await prisma.withdrawal.findUnique({ where: { id: req.params.id } });
        if (!existing) {
          res.status(404).json({ error: 'NotFound', message: 'Withdrawal not found' });
          return;
        }
        if (existing.status !== 'FAILED') {
          res.status(400).json({
            error: 'BadRequest',
            message: 'Only failed withdrawals can be retried',
          });
          return;
        }

        const withdrawal = await prisma.withdrawal.update({
          where: { id: req.params.id },
          data: { status: 'APPROVED' },
          include: {
            user: { select: { id: true, email: true, status: true } },
            assetNetwork: { include: { asset: true, network: true } },
            approvals: true,
          },
        });

        await req.auditLog?.({
          action: 'FORCE_RETRY_WITHDRAWAL',
          entityType: 'WITHDRAWAL',
          entityId: withdrawal.id,
          beforeSnapshot: existing,
          afterSnapshot: withdrawal,
          metadata: { note: req.body.note },
        });

        res.json({ success: true, data: { withdrawal } });
      } catch (error) {
        next(error);
      }
    }
  );

  return router;
}
