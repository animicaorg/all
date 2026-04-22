import { Router } from 'express';
import type { HttpContext } from '../context.js';
import type { RequestWithContext } from '../types.js';

export function createReservesRouter(ctx: HttpContext): Router {
  const router = Router();

  router.get('/reserves/dashboard', async (_req, res, next) => {
    try {
      const dashboard = await ctx.services.reserve.getDashboard();
      res.json({ dashboard });
    } catch (error) {
      next(error);
    }
  });

  router.get('/reserves/snapshots', async (_req, res, next) => {
    try {
      const snapshots = await ctx.store.listReserveSnapshots(100);
      res.json({ snapshots });
    } catch (error) {
      next(error);
    }
  });

  router.post('/reserves/snapshots/capture', async (req: RequestWithContext, res, next) => {
    try {
      const snapshot = await ctx.services.admin.publishReserveSnapshot(req.admin?.actorId ?? 'admin_api_key');
      res.status(201).json({ snapshot });
    } catch (error) {
      next(error);
    }
  });

  return router;
}
