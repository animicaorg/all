/**
 * Settings Routes
 * Manage BitGo configuration.
 */

import { Router } from 'express';
import { z } from 'zod';
import type { PrismaClient } from '@prisma/client';
import type { Config } from '../../config.js';
import type { Logger } from '../../utils/logger.js';
import { BitgoConfigService } from '../../services/bitgo_config.js';
import { validateBody } from '../middleware/validation.js';
import { requirePermission, PERMISSIONS } from '../middleware/rbac.js';

const bitgoConfigSchema = z.object({
  environment: z.enum(['test', 'prod']),
  baseUrl: z.string().url().optional().nullable(),
  accessToken: z.string().optional().nullable(),
  webhookSecret: z.string().optional().nullable(),
  wallets: z.record(z.string(), z.string()).optional().nullable(),
  coins: z.record(z.string(), z.any()).optional().nullable(),
  enabled: z.boolean(),
});

export function createSettingsRouter(
  prisma: PrismaClient,
  config: Config,
  logger: Logger
): Router {
  const router = Router();
  const bitgoService = new BitgoConfigService(prisma, config, logger);

  router.get(
    '/bitgo',
    requirePermission(PERMISSIONS.WALLETS_READ),
    async (req, res, next) => {
      try {
        const settings = await bitgoService.getConfig();
        res.json({ success: true, data: settings });
      } catch (error) {
        next(error);
      }
    }
  );

  router.put(
    '/bitgo',
    requirePermission(PERMISSIONS.WALLETS_WRITE),
    validateBody(bitgoConfigSchema),
    async (req, res, next) => {
      try {
        if (!req.admin) {
          res.status(401).json({ error: 'Unauthorized' });
          return;
        }

        const { response, before } = await bitgoService.updateConfig(req.body, req.admin.id);

        await req.auditLog?.({
          action: 'BITGO_CONFIG_UPDATED',
          entityType: 'BITGO_CONFIG',
          entityId: response.id,
          beforeSnapshot: before,
          afterSnapshot: response,
        });

        res.json({ success: true, data: response });
      } catch (error) {
        next(error);
      }
    }
  );

  router.post(
    '/bitgo/test',
    requirePermission(PERMISSIONS.WALLETS_READ),
    async (_req, res, next) => {
      try {
        const result = await bitgoService.testConnection();
        res.json({ success: result.ok, data: result });
      } catch (error) {
        next(error);
      }
    }
  );

  return router;
}
