import { Router } from 'express';
import { z } from 'zod';
import { requireAdmin, sessionAuth } from '../../middleware/auth.js';
import type { PlatformService } from '../../services/platformService.js';

export function createAgentTasksRouter(service: PlatformService) {
  const router = Router();
  router.use(sessionAuth(service));

  router.get('/', (req, res) => {
    try {
      const contractAddress = req.query.contractAddress ? String(req.query.contractAddress) : undefined;
      const tasks = service.listAgentTasks(req.ctx.user!, contractAddress);
      res.status(200).json({ tasks });
    } catch (error) {
      res.status(400).json({ error: { message: (error as Error).message } });
    }
  });

  router.post('/', (req, res) => {
    try {
      const body = z
        .object({
          contractAddress: z.string().min(8),
          requester: z.string().min(3),
          payer: z.string().min(3),
          modelId: z.string().min(3),
          budgetAnmNanos: z.string().min(1),
          onchainTaskId: z.string().optional()
        })
        .parse(req.body ?? {});
      const task = service.createAgentTask(req.ctx.user!, body);
      res.status(201).json({ task });
    } catch (error) {
      res.status(400).json({ error: { message: (error as Error).message } });
    }
  });

  router.get('/:taskId', (req, res) => {
    try {
      const task = service.getAgentTask(req.ctx.user!, req.params.taskId);
      res.status(200).json({ task });
    } catch (error) {
      res.status(404).json({ error: { message: (error as Error).message } });
    }
  });

  router.post('/:taskId/steps', (req, res) => {
    try {
      const body = z.object({ commitmentHash: z.string().min(8), traceRef: z.string().optional() }).parse(req.body ?? {});
      const task = service.appendAgentTaskStepCommitment(req.ctx.user!, req.params.taskId, body);
      res.status(200).json({ task });
    } catch (error) {
      res.status(400).json({ error: { message: (error as Error).message } });
    }
  });

  router.post('/:taskId/final-result', (req, res) => {
    try {
      const body = z.object({ resultHash: z.string().min(8), resultRef: z.string().min(4) }).parse(req.body ?? {});
      const task = service.submitAgentTaskFinalResult(req.ctx.user!, req.params.taskId, body);
      res.status(200).json({ task });
    } catch (error) {
      res.status(400).json({ error: { message: (error as Error).message } });
    }
  });

  router.post('/-admin/disputes/:disputeId/resolve', requireAdmin, (req, res) => {
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
