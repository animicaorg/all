import { Router } from 'express';
import type { PlatformService } from '../../services/platformService.js';

export function createModelsRouter(service: PlatformService) {
  const router = Router();

  router.get('/', (_req, res) => {
    res.status(200).json({
      object: 'list',
      data: service.listModels()
    });
  });

  return router;
}
