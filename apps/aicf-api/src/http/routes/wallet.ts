import { Router } from 'express';
import { z } from 'zod';
import { sessionAuth } from '../../middleware/auth.js';
import type { PlatformService } from '../../services/platformService.js';

export function createWalletRouter(service: PlatformService) {
  const router = Router();
  router.use(sessionAuth(service));

  router.post('/link', (req, res) => {
    try {
      const body = z
        .object({
          address: z.string().min(8),
          chainId: z.number().int().positive(),
          signature: z.string().min(8)
        })
        .parse(req.body ?? {});
      const user = service.linkWallet(req.ctx.user!, body);
      res.status(200).json({ user });
    } catch (error) {
      res.status(400).json({ error: { message: (error as Error).message } });
    }
  });

  router.get('/governance-config', (_req, res) => {
    res.status(200).json({ config: service.getGovernanceConfig() });
  });

  return router;
}
