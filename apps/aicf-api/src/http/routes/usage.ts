import { Router } from 'express';
import { sessionAuth } from '../../middleware/auth.js';
import type { PlatformService } from '../../services/platformService.js';

export function createUsageRouter(service: PlatformService) {
  const router = Router();
  router.use(sessionAuth(service));

  router.get('/', (req, res) => {
    try {
      const projectId = req.query.projectId ? String(req.query.projectId) : undefined;
      const usage = service.listUsage(req.ctx.user!, projectId);
      res.status(200).json({ usage });
    } catch (error) {
      res.status(400).json({ error: { message: (error as Error).message } });
    }
  });

  router.get('/settlements', (req, res) => {
    try {
      const projectId = req.query.projectId ? String(req.query.projectId) : undefined;
      const settlements = service.listSettlements(req.ctx.user!, projectId);
      res.status(200).json({ settlements });
    } catch (error) {
      res.status(400).json({ error: { message: (error as Error).message } });
    }
  });

  return router;
}
