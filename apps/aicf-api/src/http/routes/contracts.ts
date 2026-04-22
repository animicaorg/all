import { Router } from 'express';
import { z } from 'zod';
import { sessionAuth } from '../../middleware/auth.js';
import type { PlatformService } from '../../services/platformService.js';

export function createContractsRouter(service: PlatformService) {
  const router = Router();
  router.use(sessionAuth(service));

  router.get('/', (req, res) => {
    try {
      const contracts = service.listContracts(req.ctx.user!);
      res.status(200).json({ contracts });
    } catch (error) {
      res.status(400).json({ error: { message: (error as Error).message } });
    }
  });

  router.post('/', (req, res) => {
    try {
      const body = z
        .object({
          address: z.string().min(8),
          type: z.enum(['model_call', 'agent_task', 'ai_escrow', 'custom']),
          metadata: z.object({
            name: z.string().min(2),
            description: z.string().optional(),
            tags: z.array(z.string()).optional(),
            abiRef: z.string().optional(),
            sourceRef: z.string().optional()
          })
        })
        .parse(req.body ?? {});

      const contract = service.registerContract(req.ctx.user!, body);
      res.status(201).json({ contract });
    } catch (error) {
      res.status(400).json({ error: { message: (error as Error).message } });
    }
  });

  router.put('/:address/artifacts', (req, res) => {
    try {
      const body = z
        .object({
          sourceCode: z.string().optional(),
          sourceLanguage: z.string().optional(),
          abiJson: z.string().optional()
        })
        .parse(req.body ?? {});
      const payload = service.upsertContractArtifacts(req.ctx.user!, req.params.address, body);
      res.status(200).json(payload);
    } catch (error) {
      res.status(400).json({ error: { message: (error as Error).message } });
    }
  });

  router.get('/artifacts/by-ref', (req, res) => {
    try {
      const ref = String(req.query.ref ?? '').trim();
      if (!ref) {
        throw new Error('Missing artifact ref');
      }
      const artifact = service.getContractArtifactByRef(req.ctx.user!, ref);
      res.status(200).json({ artifact });
    } catch (error) {
      res.status(400).json({ error: { message: (error as Error).message } });
    }
  });

  router.get('/:address', (req, res) => {
    try {
      const contract = service.getContract(req.ctx.user!, req.params.address);
      res.status(200).json({ contract });
    } catch (error) {
      res.status(404).json({ error: { message: (error as Error).message } });
    }
  });

  router.post('/:address/pause', (req, res) => {
    try {
      const body = z.object({ paused: z.boolean() }).parse(req.body ?? {});
      const contract = service.setContractPaused(req.ctx.user!, req.params.address, body.paused);
      res.status(200).json({ contract });
    } catch (error) {
      res.status(400).json({ error: { message: (error as Error).message } });
    }
  });

  return router;
}
