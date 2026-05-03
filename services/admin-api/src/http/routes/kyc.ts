/**
 * KYC Routes
 * Review and update user identity cases.
 */

import { Router } from 'express';
import { z } from 'zod';
import type { PrismaClient } from '@prisma/client';
import type { Config } from '../../config.js';
import type { Logger } from '../../utils/logger.js';
import { validateBody, validateParams, validateQuery, commonSchemas } from '../middleware/validation.js';
import { requirePermission, PERMISSIONS } from '../middleware/rbac.js';
import { pagination, tableExists } from './db_helpers.js';

const kycQuerySchema = z.object({
  query: z.string().optional(),
  status: z.enum(['NOT_STARTED', 'PENDING', 'VERIFIED', 'REJECTED', 'REVIEW']).optional(),
  riskTier: z.enum(['LOW', 'MEDIUM', 'HIGH']).optional(),
  ...commonSchemas.paginationQuery.shape,
});

const reviewSchema = z.object({
  action: z.enum(['approve', 'reject', 'request_info']),
  notes: z.string().max(2000).optional(),
  riskTier: z.enum(['LOW', 'MEDIUM', 'HIGH']).optional(),
});

function isUuid(value: string): boolean {
  return /^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i.test(value);
}

export function createKycRouter(
  prisma: PrismaClient,
  _config: Config,
  _logger: Logger
): Router {
  const router = Router();

  router.get(
    '/',
    requirePermission(PERMISSIONS.KYC_READ),
    validateQuery(kycQuerySchema),
    async (req, res, next) => {
      try {
        const { query, status, riskTier, page = 1, limit = 50 } = req.query as any;
        if (!(await tableExists(prisma, 'kyc_cases'))) {
          res.json({
            success: true,
            data: {
              cases: [],
              queueCounts: [],
              pagination: pagination(page, limit, 0),
            },
          });
          return;
        }

        const where: any = {};
        if (status) where.status = status;
        if (riskTier) where.riskTier = riskTier;
        if (query) {
          where.OR = [
            { user: { email: { contains: query, mode: 'insensitive' } } },
            {
              user: {
                profile: {
                  is: { legalName: { contains: query, mode: 'insensitive' } },
                },
              },
            },
          ];
          if (isUuid(query)) {
            where.OR.push({ id: query }, { userId: query });
          }
        }

        const [cases, total, queueCounts] = await Promise.all([
          prisma.kycCase.findMany({
            where,
            include: {
              user: {
                select: {
                  id: true,
                  email: true,
                  status: true,
                  createdAt: true,
                  profile: true,
                },
              },
              documents: {
                select: {
                  id: true,
                  docType: true,
                  storageRef: true,
                  sha256: true,
                  createdAt: true,
                },
                orderBy: { createdAt: 'desc' },
              },
            },
            orderBy: [{ submittedAt: 'asc' }, { createdAt: 'asc' }],
            skip: (page - 1) * limit,
            take: limit,
          }),
          prisma.kycCase.count({ where }),
          prisma.kycCase.groupBy({
            by: ['status'],
            _count: { _all: true },
          }),
        ]);

        res.json({
          success: true,
          data: {
            cases,
            queueCounts: queueCounts.map((row) => ({
              status: row.status,
              count: row._count._all,
            })),
            pagination: pagination(page, limit, total),
          },
        });
      } catch (error) {
        next(error);
      }
    }
  );

  router.patch(
    '/:id/review',
    requirePermission(PERMISSIONS.KYC_REVIEW),
    validateParams(z.object({ id: commonSchemas.uuid })),
    validateBody(reviewSchema),
    async (req, res, next) => {
      try {
        const existing = await prisma.kycCase.findUnique({
          where: { id: req.params.id },
        });

        if (!existing) {
          res.status(404).json({ error: 'NotFound', message: 'KYC case not found' });
          return;
        }

        const { action, notes, riskTier } = req.body;
        const status =
          action === 'approve' ? 'VERIFIED' : action === 'reject' ? 'REJECTED' : 'REVIEW';

        const updated = await prisma.kycCase.update({
          where: { id: req.params.id },
          data: {
            status,
            riskTier: riskTier ?? existing.riskTier,
            notes: notes ?? existing.notes,
            reviewedAt: new Date(),
          },
          include: {
            user: { select: { id: true, email: true, status: true, profile: true } },
            documents: true,
          },
        });

        await req.auditLog?.({
          action:
            action === 'approve'
              ? 'APPROVE_KYC'
              : action === 'reject'
                ? 'REJECT_KYC'
                : 'REQUEST_KYC_INFO',
          entityType: 'KYC_CASE',
          entityId: updated.id,
          beforeSnapshot: existing,
          afterSnapshot: updated,
          metadata: { notes, riskTier },
        });

        res.json({ success: true, data: { case: updated } });
      } catch (error) {
        next(error);
      }
    }
  );

  return router;
}
