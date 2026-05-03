/**
 * Incident Routes
 * Operational incident tracking and action log.
 */

import { Router } from 'express';
import { z } from 'zod';
import type { PrismaClient } from '@prisma/client';
import type { Config } from '../../config.js';
import type { Logger } from '../../utils/logger.js';
import { validateBody, validateParams, validateQuery, commonSchemas } from '../middleware/validation.js';
import { requirePermission, PERMISSIONS } from '../middleware/rbac.js';
import { pagination, tableExists } from './db_helpers.js';

const incidentQuerySchema = z.object({
  status: z.enum(['OPEN', 'IN_PROGRESS', 'RESOLVED', 'CLOSED']).optional(),
  severity: z.enum(['LOW', 'MEDIUM', 'HIGH', 'CRITICAL']).optional(),
  query: z.string().optional(),
  ...commonSchemas.paginationQuery.shape,
});

const createIncidentSchema = z.object({
  title: z.string().min(3).max(200),
  severity: z.enum(['LOW', 'MEDIUM', 'HIGH', 'CRITICAL']),
});

const updateIncidentSchema = z.object({
  title: z.string().min(3).max(200).optional(),
  severity: z.enum(['LOW', 'MEDIUM', 'HIGH', 'CRITICAL']).optional(),
  status: z.enum(['OPEN', 'IN_PROGRESS', 'RESOLVED', 'CLOSED']).optional(),
});

const actionSchema = z.object({
  action: z.string().min(2).max(120),
  status: z.string().min(2).max(80).default('completed'),
  payload: z.any().optional().nullable(),
});

export function createIncidentsRouter(
  prisma: PrismaClient,
  _config: Config,
  _logger: Logger
): Router {
  const router = Router();

  router.get(
    '/',
    requirePermission(PERMISSIONS.INCIDENTS_READ),
    validateQuery(incidentQuerySchema),
    async (req, res, next) => {
      try {
        const { status, severity, query, page = 1, limit = 50 } = req.query as any;
        if (!(await tableExists(prisma, 'incidents'))) {
          res.json({
            success: true,
            data: {
              incidents: [],
              statusCounts: [],
              pagination: pagination(page, limit, 0),
            },
          });
          return;
        }

        const where: any = {};
        if (status) where.status = status;
        if (severity) where.severity = severity;
        if (query) where.title = { contains: query, mode: 'insensitive' };

        const [incidents, total, statusCounts] = await Promise.all([
          prisma.incident.findMany({
            where,
            include: {
              creator: { select: { id: true, email: true, role: true } },
              actions: {
                include: {
                  creator: { select: { id: true, email: true, role: true } },
                },
                orderBy: { createdAt: 'desc' },
                take: 20,
              },
            },
            orderBy: [{ status: 'asc' }, { createdAt: 'desc' }],
            skip: (page - 1) * limit,
            take: limit,
          }),
          prisma.incident.count({ where }),
          prisma.incident.groupBy({
            by: ['status'],
            _count: { _all: true },
          }),
        ]);

        res.json({
          success: true,
          data: {
            incidents,
            statusCounts: statusCounts.map((row) => ({
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

  router.post(
    '/',
    requirePermission(PERMISSIONS.INCIDENTS_EXECUTE),
    validateBody(createIncidentSchema),
    async (req, res, next) => {
      try {
        const incident = await prisma.incident.create({
          data: {
            title: req.body.title,
            severity: req.body.severity,
            createdBy: req.admin!.id,
          },
          include: { creator: { select: { id: true, email: true, role: true } }, actions: true },
        });

        await req.auditLog?.({
          action: 'CREATE_INCIDENT',
          entityType: 'INCIDENT',
          entityId: incident.id,
          afterSnapshot: incident,
        });

        res.status(201).json({ success: true, data: { incident } });
      } catch (error) {
        next(error);
      }
    }
  );

  router.patch(
    '/:id',
    requirePermission(PERMISSIONS.INCIDENTS_EXECUTE),
    validateParams(z.object({ id: commonSchemas.uuid })),
    validateBody(updateIncidentSchema),
    async (req, res, next) => {
      try {
        const existing = await prisma.incident.findUnique({ where: { id: req.params.id } });
        if (!existing) {
          res.status(404).json({ error: 'NotFound', message: 'Incident not found' });
          return;
        }

        const now = new Date();
        const incident = await prisma.incident.update({
          where: { id: req.params.id },
          data: {
            title: req.body.title,
            severity: req.body.severity,
            status: req.body.status,
            resolvedAt: req.body.status === 'RESOLVED' ? now : undefined,
            closedAt: req.body.status === 'CLOSED' ? now : undefined,
          },
          include: {
            creator: { select: { id: true, email: true, role: true } },
            actions: { orderBy: { createdAt: 'desc' } },
          },
        });

        await req.auditLog?.({
          action: 'UPDATE_INCIDENT',
          entityType: 'INCIDENT',
          entityId: incident.id,
          beforeSnapshot: existing,
          afterSnapshot: incident,
        });

        res.json({ success: true, data: { incident } });
      } catch (error) {
        next(error);
      }
    }
  );

  router.post(
    '/:id/actions',
    requirePermission(PERMISSIONS.INCIDENTS_EXECUTE),
    validateParams(z.object({ id: commonSchemas.uuid })),
    validateBody(actionSchema),
    async (req, res, next) => {
      try {
        const incident = await prisma.incident.findUnique({ where: { id: req.params.id } });
        if (!incident) {
          res.status(404).json({ error: 'NotFound', message: 'Incident not found' });
          return;
        }

        const action = await prisma.incidentAction.create({
          data: {
            incidentId: req.params.id,
            action: req.body.action,
            status: req.body.status,
            payload: req.body.payload ?? null,
            completedAt: req.body.status === 'completed' ? new Date() : null,
            createdBy: req.admin!.id,
          },
          include: { creator: { select: { id: true, email: true, role: true } } },
        });

        await req.auditLog?.({
          action: 'INCIDENT_ACTION',
          entityType: 'INCIDENT',
          entityId: req.params.id,
          afterSnapshot: action,
        });

        res.status(201).json({ success: true, data: { action } });
      } catch (error) {
        next(error);
      }
    }
  );

  return router;
}
