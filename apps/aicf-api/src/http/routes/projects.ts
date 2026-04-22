import { Router } from 'express';
import { z } from 'zod';
import { sessionAuth } from '../../middleware/auth.js';
import type { PlatformService } from '../../services/platformService.js';

export function createProjectsRouter(service: PlatformService) {
  const router = Router();

  router.use(sessionAuth(service));

  router.get('/', (req, res) => {
    try {
      const projects = service.listProjects(req.ctx.user!);
      res.status(200).json({ projects });
    } catch (error) {
      res.status(400).json({ error: { message: (error as Error).message } });
    }
  });

  router.post('/', (req, res) => {
    try {
      const body = z
        .object({
          name: z.string().min(2),
          slug: z.string().min(2),
          description: z.string().optional()
        })
        .parse(req.body ?? {});
      const project = service.createProject(req.ctx.user!, body);
      res.status(201).json({ project });
    } catch (error) {
      res.status(400).json({ error: { message: (error as Error).message } });
    }
  });

  router.patch('/:projectId', (req, res) => {
    try {
      const body = z
        .object({
          description: z.string().optional(),
          webhookUrl: z.string().url().optional()
        })
        .parse(req.body ?? {});
      const project = service.updateProject(req.ctx.user!, req.params.projectId, body);
      res.status(200).json({ project });
    } catch (error) {
      res.status(400).json({ error: { message: (error as Error).message } });
    }
  });

  router.post('/:projectId/fund', (req, res) => {
    try {
      const body = z
        .object({
          amountAnm: z.string().min(1),
          txHash: z.string().optional()
        })
        .parse(req.body ?? {});
      const result = service.createFundingIntent(req.ctx.user!, {
        projectId: req.params.projectId,
        amountAnm: body.amountAnm,
        txHash: body.txHash
      });
      res.status(200).json(result);
    } catch (error) {
      res.status(400).json({ error: { message: (error as Error).message } });
    }
  });

  router.post('/:projectId/withdraw', (req, res) => {
    try {
      const body = z
        .object({
          amountAnmNanos: z.string().min(1)
        })
        .parse(req.body ?? {});
      const project = service.withdrawProjectBalance(req.ctx.user!, {
        projectId: req.params.projectId,
        amountAnmNanos: body.amountAnmNanos
      });
      res.status(200).json({ project });
    } catch (error) {
      res.status(400).json({ error: { message: (error as Error).message } });
    }
  });

  router.get('/:projectId/api-keys', (req, res) => {
    try {
      const apiKeys = service.listApiKeys(req.ctx.user!, req.params.projectId);
      res.status(200).json({ apiKeys });
    } catch (error) {
      res.status(404).json({ error: { message: (error as Error).message } });
    }
  });

  router.post('/:projectId/api-keys', (req, res) => {
    try {
      const body = z
        .object({
          name: z.string().min(2),
          scopes: z
            .array(
              z.enum([
                'inference:chat',
                'inference:embeddings',
                'jobs:write',
                'jobs:read',
                'projects:read',
                'projects:write'
              ])
            )
            .min(1)
        })
        .parse(req.body ?? {});
      const result = service.createApiKey(req.ctx.user!, {
        projectId: req.params.projectId,
        name: body.name,
        scopes: body.scopes
      });
      res.status(201).json({
        keyId: result.key.id,
        prefix: result.key.prefix,
        token: result.token,
        scopes: result.key.scopes,
        createdAt: result.key.createdAt
      });
    } catch (error) {
      res.status(400).json({ error: { message: (error as Error).message } });
    }
  });

  router.post('/api-keys/:keyId/revoke', (req, res) => {
    try {
      const key = service.revokeApiKey(req.ctx.user!, req.params.keyId);
      res.status(200).json({ key });
    } catch (error) {
      res.status(404).json({ error: { message: (error as Error).message } });
    }
  });

  return router;
}
