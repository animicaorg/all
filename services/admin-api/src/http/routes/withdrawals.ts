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

const withdrawalQuerySchema = z.object({
  query: z.string().optional(),
  status: z
    .enum(['REQUESTED', 'RISK_REVIEW', 'APPROVED', 'SIGNING', 'BROADCAST', 'CONFIRMED', 'FAILED', 'CANCELED'])
    .optional(),
  provider: z.enum(['BITGO', 'ANIMICA_NODE', 'MANUAL']).optional(),
  ...commonSchemas.paginationQuery.shape,
});

const noteSchema = z.object({
  note: z.string().max(1000).optional(),
});

function isUuid(value: string): boolean {
  return /^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i.test(value);
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
        const where: any = {};
        if (status) where.status = status;
        if (provider) where.provider = provider;
        if (query) {
          where.OR = [
            { txid: { contains: query, mode: 'insensitive' } },
            { destinationAddress: { contains: query, mode: 'insensitive' } },
            { user: { email: { contains: query, mode: 'insensitive' } } },
          ];
          if (isUuid(query)) {
            where.OR.push({ id: query }, { userId: query });
          }
        }

        const [withdrawals, total, statusCounts] = await Promise.all([
          prisma.withdrawal.findMany({
            where,
            include: {
              user: { select: { id: true, email: true, status: true } },
              assetNetwork: {
                include: {
                  asset: true,
                  network: true,
                },
              },
              approvals: {
                include: {
                  approverAdmin: { select: { id: true, email: true, role: true } },
                  approverUser: { select: { id: true, email: true } },
                },
                orderBy: { createdAt: 'desc' },
              },
            },
            orderBy: { requestedAt: 'desc' },
            skip: (page - 1) * limit,
            take: limit,
          }),
          prisma.withdrawal.count({ where }),
          prisma.withdrawal.groupBy({
            by: ['status'],
            _count: { _all: true },
          }),
        ]);

        res.json({
          success: true,
          data: {
            withdrawals,
            statusCounts: statusCounts.map((row) => ({
              status: row.status,
              count: row._count._all,
            })),
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
