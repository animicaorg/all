import { Router } from 'express';
import { z } from 'zod';
import { sessionAuth } from '../../middleware/auth.js';
import type { PlatformService } from '../../services/platformService.js';

export function createContractJobsRouter(service: PlatformService) {
  const router = Router();
  router.use(sessionAuth(service));

  router.get('/', (req, res) => {
    try {
      const jobs = service.listContractJobs(req.ctx.user!, {
        contractAddress: req.query.contractAddress ? String(req.query.contractAddress) : undefined,
        state: req.query.state ? (String(req.query.state) as never) : undefined,
        modelId: req.query.modelId ? String(req.query.modelId) : undefined,
        providerId: req.query.providerId ? String(req.query.providerId) : undefined,
        disputeStatus: req.query.disputeStatus ? (String(req.query.disputeStatus) as never) : undefined
      });
      res.status(200).json({ jobs });
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
          jobType: z.enum(['model_call', 'agent_task', 'embedding', 'classification', 'custom']),
          inputRefHash: z.string().min(6),
          outputSchemaRef: z.string().optional(),
          maxBudgetAnmNanos: z.string().min(1),
          timeoutSeconds: z.number().int().positive().max(7 * 24 * 3600),
          replication: z.number().int().min(1).max(8),
          quorum: z.number().int().min(1).max(8),
          verificationMode: z.enum(['SINGLE_PROVIDER', 'QUORUM_MATCH', 'VERIFIER_REVIEW', 'CALLBACK_ACCEPT']),
          challengeWindowSeconds: z.number().int().positive().max(7 * 24 * 3600),
          providerPolicy: z.object({
            mode: z.enum(['open', 'allowlist', 'blocklist']),
            providerIds: z.array(z.string()).default([])
          }),
          privacy: z.enum(['public', 'private']),
          callbackMode: z.enum(['none', 'requester_accept', 'contract_callback']),
          resultType: z.enum([
            'text',
            'json',
            'embeddings_artifact',
            'classification_labels',
            'tool_trace_bundle',
            'agent_transcript_bundle'
          ]),
          metadata: z.record(z.unknown()).optional(),
          onchainJobId: z.string().optional(),
          txHash: z.string().optional()
        })
        .parse(req.body ?? {});

      const created = service.createContractJob(req.ctx.user!, body);
      res.status(201).json(created);
    } catch (error) {
      res.status(400).json({ error: { message: (error as Error).message } });
    }
  });

  router.get('/-meta/disputes', (req, res) => {
    try {
      const status = req.query.status ? (String(req.query.status) as 'open' | 'resolved' | 'dismissed') : undefined;
      const disputes = service.listContractDisputes(req.ctx.user!, status);
      res.status(200).json({ disputes });
    } catch (error) {
      res.status(400).json({ error: { message: (error as Error).message } });
    }
  });

  router.get('/-meta/escrow-events', (req, res) => {
    try {
      const jobId = req.query.jobId ? String(req.query.jobId) : undefined;
      const events = service.listContractEscrowEvents(req.ctx.user!, jobId);
      res.status(200).json({ events });
    } catch (error) {
      res.status(400).json({ error: { message: (error as Error).message } });
    }
  });

  router.get('/:jobId', (req, res) => {
    try {
      const job = service.getContractJob(req.ctx.user!, req.params.jobId);
      const commitments = service.listContractJobCommitments(req.ctx.user!, req.params.jobId);
      const assignments = service.listContractJobAssignments(req.ctx.user!, req.params.jobId);
      const escrowEvents = service.listContractEscrowEvents(req.ctx.user!, req.params.jobId);
      res.status(200).json({ job, commitments, assignments, escrowEvents });
    } catch (error) {
      res.status(404).json({ error: { message: (error as Error).message } });
    }
  });

  router.post('/:jobId/accept', (req, res) => {
    try {
      const body = z.object({ acceptedHash: z.string().optional() }).parse(req.body ?? {});
      const job = service.acceptContractResult(req.ctx.user!, req.params.jobId, body.acceptedHash);
      res.status(200).json({ job });
    } catch (error) {
      res.status(400).json({ error: { message: (error as Error).message } });
    }
  });

  router.post('/:jobId/disputes', (req, res) => {
    try {
      const body = z
        .object({
          reasonCode: z.string().min(3),
          evidenceRef: z.string().optional()
        })
        .parse(req.body ?? {});
      const result = service.openContractDispute(req.ctx.user!, {
        jobId: req.params.jobId,
        reasonCode: body.reasonCode,
        evidenceRef: body.evidenceRef
      });
      res.status(202).json(result);
    } catch (error) {
      res.status(400).json({ error: { message: (error as Error).message } });
    }
  });

  router.post('/:jobId/finalize', (req, res) => {
    try {
      const result = service.finalizeContractJob(req.ctx.user!, req.params.jobId);
      res.status(200).json(result);
    } catch (error) {
      res.status(400).json({ error: { message: (error as Error).message } });
    }
  });

  router.post('/:jobId/refund-if-expired', (req, res) => {
    try {
      const job = service.refundContractJobIfExpired(req.ctx.user!, req.params.jobId);
      res.status(200).json({ job });
    } catch (error) {
      res.status(400).json({ error: { message: (error as Error).message } });
    }
  });

  return router;
}
