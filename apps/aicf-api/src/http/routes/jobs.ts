import { Router } from 'express';
import { z } from 'zod';
import { sessionAuth } from '../../middleware/auth.js';
import type { PlatformService } from '../../services/platformService.js';

const jobClassSchema = z.enum([
  'chat_inference',
  'embedding_generation',
  'batch_inference',
  'fine_tuning_training',
  'evaluation',
  'retrieval_indexing',
  'agent_task',
  'custom_compute'
]);

export function createJobsRouter(service: PlatformService) {
  const router = Router();
  router.use(sessionAuth(service));

  router.post('/', (req, res) => {
    try {
      const body = z
        .object({
          projectId: z.string().min(3),
          apiKeyId: z.string().optional(),
          maxBudgetAnmNanos: z.string().min(1),
          subsidyBps: z.number().int().min(0).max(10000).optional(),
          request: z.object({
            class: jobClassSchema,
            model: z.string().min(3),
            input: z.record(z.unknown()),
            timeoutSeconds: z.number().int().positive().max(7 * 24 * 3600),
            replication: z.number().int().min(1).max(5),
            verificationMode: z.enum(['none', 'sampled', 'full']),
            outputMode: z.enum(['private', 'public']),
            callbackUrl: z.string().url().optional(),
            challengeWindowSeconds: z.number().int().positive().max(7 * 24 * 3600),
            regionPreference: z.string().optional(),
            requiredHardware: z
              .object({
                minGpuMemoryGb: z.number().int().positive().optional(),
                minCpu: z.number().int().positive().optional(),
                minRamGb: z.number().int().positive().optional()
              })
              .optional()
          })
        })
        .parse(req.body ?? {});

      const job = service.createAsyncJob(req.ctx.user!, body);
      res.status(201).json({ job });
    } catch (error) {
      res.status(400).json({ error: { message: (error as Error).message } });
    }
  });

  router.get('/', (req, res) => {
    try {
      const projectId = req.query.projectId ? String(req.query.projectId) : undefined;
      const jobs = service.listJobs(req.ctx.user!, projectId);
      res.status(200).json({ jobs });
    } catch (error) {
      res.status(400).json({ error: { message: (error as Error).message } });
    }
  });

  router.post('/:jobId/disputes', (req, res) => {
    try {
      const body = z.object({ reason: z.string().min(6) }).parse(req.body ?? {});
      const job = service.openDispute(req.ctx.user!, {
        jobId: req.params.jobId,
        reason: body.reason
      });
      res.status(202).json({ job });
    } catch (error) {
      res.status(400).json({ error: { message: (error as Error).message } });
    }
  });

  return router;
}
