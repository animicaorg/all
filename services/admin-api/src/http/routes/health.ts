/**
 * Health Routes
 * System health checks
 */

import { Router } from 'express';
import type { PrismaClient } from '@prisma/client';
import type { Config } from '../../config.js';
import type { Logger } from '../../utils/logger.js';

export function createHealthRouter(
  prisma: PrismaClient,
  config: Config,
  logger: Logger
): Router {
  const router = Router();

  /**
   * GET /admin/v1/health
   * Detailed health check
   */
  router.get('/', async (req, res) => {
    const health: any = {
      status: 'ok',
      service: config.SERVICE_NAME,
      timestamp: new Date().toISOString(),
      checks: {},
    };

    try {
      // Check database
      await prisma.$queryRaw`SELECT 1`;
      health.checks.database = { status: 'ok' };
    } catch (error) {
      health.checks.database = { status: 'error', message: 'Database connection failed' };
      health.status = 'degraded';
    }

    // Check if any admins exist
    try {
      const adminCount = await prisma.admin.count();
      health.checks.admins = {
        status: adminCount > 0 ? 'ok' : 'warning',
        count: adminCount,
      };
    } catch {
      health.checks.admins = { status: 'error' };
    }

    res.status(health.status === 'ok' ? 200 : 503).json(health);
  });

  return router;
}
