import { Router } from 'express';
import { z } from 'zod';
import type { HttpContext } from '../context.js';
import type { RequestWithContext } from '../types.js';

const AddFlagSchema = z.object({
  userId: z.string().min(3),
  type: z.enum(['SANCTIONS', 'AML', 'MANUAL_REVIEW', 'VELOCITY', 'LIMIT']),
  reason: z.string().min(3)
});

const SetKycSchema = z.object({
  userId: z.string().min(3),
  status: z.enum(['NOT_STARTED', 'PENDING', 'APPROVED', 'REJECTED', 'REVIEW']),
  provider: z.string().min(2).default('manual')
});

export function createAdminRouter(ctx: HttpContext): Router {
  const router = Router();

  router.get('/admin/purchases', async (_req, res, next) => {
    try {
      const purchases = await ctx.store.listPurchaseIntents();
      res.json({ purchases });
    } catch (error) {
      next(error);
    }
  });

  router.get('/admin/redemptions', async (_req, res, next) => {
    try {
      const redemptions = await ctx.store.listRedemptionRequests();
      res.json({ redemptions });
    } catch (error) {
      next(error);
    }
  });

  router.get('/admin/webhooks', async (_req, res, next) => {
    try {
      const webhooks = await ctx.store.listWebhookDeliveries();
      res.json({ webhooks });
    } catch (error) {
      next(error);
    }
  });

  router.post('/admin/compliance/flags', async (req: RequestWithContext, res, next) => {
    try {
      const input = AddFlagSchema.parse(req.body);
      const flag = await ctx.services.admin.addComplianceFlag({
        userId: input.userId,
        type: input.type,
        reason: input.reason,
        actorId: req.admin?.actorId ?? 'admin_api_key'
      });
      res.status(201).json({ flag });
    } catch (error) {
      next(error);
    }
  });

  router.post('/admin/kyc/set-status', async (req: RequestWithContext, res, next) => {
    try {
      const input = SetKycSchema.parse(req.body);
      const status = await ctx.services.kyc.setStatus(input.userId, input.status, input.provider);
      res.json({ userId: input.userId, status });
    } catch (error) {
      next(error);
    }
  });

  router.post('/admin/reserves/publish', async (req: RequestWithContext, res, next) => {
    try {
      const snapshot = await ctx.services.admin.publishReserveSnapshot(req.admin?.actorId ?? 'admin_api_key');
      res.status(201).json({ snapshot });
    } catch (error) {
      next(error);
    }
  });

  return router;
}
