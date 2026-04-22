import { Router } from 'express';
import type { PlatformService } from '../../services/platformService.js';

export function createStatusRouter(service: PlatformService) {
  const router = Router();

  router.get('/', (_req, res) => {
    res.status(200).json({
      status: 'ok',
      timestamp: new Date().toISOString(),
      paused: service.getGovernanceConfig().paused,
      health: service.health()
    });
  });

  return router;
}
