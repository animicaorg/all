import { Router } from 'express';
import { z } from 'zod';
import { requireAdmin, sessionAuth } from '../../middleware/auth.js';
import type { PlatformService } from '../../services/platformService.js';

export function createDisputesRouter(service: PlatformService) {
  const router = Router();
  router.use(sessionAuth(service));

  router.get('/', (req, res) => {
    try {
      const status = req.query.status ? (String(req.query.status) as 'open' | 'resolved' | 'dismissed') : undefined;
      const disputes = service.listContractDisputes(req.ctx.user!, status);
      res.status(200).json({ disputes });
    } catch (error) {
      res.status(400).json({ error: { message: (error as Error).message } });
    }
  });

  router.post('/:disputeId/resolve', requireAdmin, (req, res) => {
    try {
      const body = z
        .object({
          action: z.enum(['slash', 'clear', 'refund_requester']),
          slashAmountAnmNanos: z.string().optional(),
          note: z.string().optional()
        })
        .parse(req.body ?? {});
      const result = service.resolveContractDispute(req.ctx.user!, {
        disputeId: req.params.disputeId,
        action: body.action,
        slashAmountAnmNanos: body.slashAmountAnmNanos,
        note: body.note
      });
      res.status(200).json(result);
    } catch (error) {
      res.status(400).json({ error: { message: (error as Error).message } });
    }
  });

  return router;
}
