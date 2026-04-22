import { Router } from 'express';
import type { HttpContext } from '../context.js';

export function createWebhookRouter(ctx: HttpContext): Router {
  const router = Router();

  router.post('/webhooks/modern-treasury', async (req, res, next) => {
    try {
      const rawBody =
        typeof (req as any).rawBody === 'string'
          ? (req as any).rawBody
          : JSON.stringify(req.body ?? {});
      const signature = req.header('x-signature') ?? req.header('modern-treasury-signature') ?? '';
      await ctx.services.webhook.processModernTreasuryWebhook(rawBody, signature);
      res.status(202).json({ accepted: true });
    } catch (error) {
      next(error);
    }
  });

  return router;
}
