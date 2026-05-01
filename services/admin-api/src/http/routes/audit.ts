/**
 * Audit Routes
 * Searchable admin audit trail.
 */

import { Router } from 'express';
import { z } from 'zod';
import type { PrismaClient } from '@prisma/client';
import type { Config } from '../../config.js';
import type { Logger } from '../../utils/logger.js';
import { validateQuery, commonSchemas } from '../middleware/validation.js';
import { requirePermission, PERMISSIONS } from '../middleware/rbac.js';

const auditQuerySchema = z.object({
  actor: z.string().optional(),
  action: z.string().optional(),
  entityType: z.string().optional(),
  entityId: z.string().optional(),
  from: z.coerce.date().optional(),
  to: z.coerce.date().optional(),
  ...commonSchemas.paginationQuery.shape,
});

function isUuid(value: string): boolean {
  return /^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i.test(value);
}

export function createAuditRouter(
  prisma: PrismaClient,
  _config: Config,
  _logger: Logger
): Router {
  const router = Router();

  router.get(
    '/',
    requirePermission(PERMISSIONS.AUDIT_READ),
    validateQuery(auditQuerySchema),
    async (req, res, next) => {
      try {
        const { actor, action, entityType, entityId, from, to, page = 1, limit = 50 } =
          req.query as any;

        const where: any = {};
        if (action) {
          where.action = { contains: action, mode: 'insensitive' };
        }
        if (entityType) {
          where.entityType = { equals: entityType, mode: 'insensitive' };
        }
        if (entityId) {
          where.entityId = entityId;
        }
        if (from || to) {
          where.createdAt = {};
          if (from) where.createdAt.gte = from;
          if (to) where.createdAt.lte = to;
        }
        if (actor) {
          where.OR = [
            { actorAdmin: { email: { contains: actor, mode: 'insensitive' } } },
            { actor: { email: { contains: actor, mode: 'insensitive' } } },
          ];
          if (isUuid(actor)) {
            where.OR.push({ actorUserId: actor }, { actorAdminId: actor });
          }
        }

        const [logs, total] = await Promise.all([
          prisma.auditLog.findMany({
            where,
            include: {
              actorAdmin: { select: { id: true, email: true, role: true } },
              actor: { select: { id: true, email: true, role: true } },
            },
            orderBy: { createdAt: 'desc' },
            skip: (page - 1) * limit,
            take: limit,
          }),
          prisma.auditLog.count({ where }),
        ]);

        res.json({
          success: true,
          data: {
            logs,
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

  return router;
}
