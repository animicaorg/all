import { Router } from 'express';
import type { HttpContext } from '../context.js';

export function createHealthRouter(ctx: HttpContext): Router {
  const router = Router();

  router.get('/healthz', async (_req, res) => {
    const snapshots = await ctx.store.listReserveSnapshots(1);
    res.json({
      status: 'ok',
      service: 'usdan-api',
      latestReserveSnapshot: snapshots[0]?.capturedAt ?? null,
      timestamp: new Date().toISOString()
    });
  });

  return router;
}
