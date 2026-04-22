import { Router } from 'express';
import { z } from 'zod';
import { requireAdmin, sessionAuth } from '../../middleware/auth.js';
import type { PlatformService } from '../../services/platformService.js';

export function createAdminRouter(service: PlatformService) {
  const router = Router();

  router.use(sessionAuth(service));
  router.use(requireAdmin);

  router.get('/overview', (_req, res) => {
    const providers = service.listProviders(_req.ctx.user!);
    const jobs = service.listJobs(_req.ctx.user!);
    const disputes = jobs.filter((job) => job.status === 'disputed');
    res.status(200).json({
      treasury: service.listTreasurySnapshot(),
      providers: {
        total: providers.length,
        active: providers.filter((provider) => provider.state === 'active').length,
        quarantined: providers.filter((provider) => provider.state === 'quarantined').length
      },
      jobs: {
        total: jobs.length,
        queued: jobs.filter((job) => job.status === 'queued').length,
        running: jobs.filter((job) => job.status === 'running').length,
        disputed: disputes.length
      }
    });
  });

  router.get('/providers', (req, res) => {
    const providers = service.listProviders(req.ctx.user!);
    const withRewards = providers.map((provider) => ({
      ...provider,
      rewardBalanceAnmNanos: service.providerRewardBalance(provider.id)
    }));
    res.status(200).json({ providers: withRewards });
  });

  router.post('/providers/:providerId/state', (req, res) => {
    try {
      const body = z
        .object({
          state: z.enum(['active', 'inactive', 'quarantined']),
          note: z.string().optional()
        })
        .parse(req.body ?? {});
      const provider = service.setProviderState(req.ctx.user!, req.params.providerId, body.state, body.note);
      res.status(200).json({ provider });
    } catch (error) {
      res.status(400).json({ error: { message: (error as Error).message } });
    }
  });

  router.get('/jobs', (req, res) => {
    const jobs = service.listJobs(req.ctx.user!);
    res.status(200).json({ jobs });
  });

  router.get('/contract-jobs', (req, res) => {
    try {
      const jobs = service.listContractJobs(req.ctx.user!, {
        state: req.query.state ? (String(req.query.state) as never) : undefined
      });
      res.status(200).json({ jobs });
    } catch (error) {
      res.status(400).json({ error: { message: (error as Error).message } });
    }
  });

  router.get('/disputes', (req, res) => {
    const jobs = service.listJobs(req.ctx.user!).filter((job) => job.status === 'disputed');
    res.status(200).json({ disputes: jobs });
  });

  router.get('/contract-disputes', (req, res) => {
    try {
      const disputes = service.listContractDisputes(
        req.ctx.user!,
        req.query.status ? (String(req.query.status) as 'open' | 'resolved' | 'dismissed') : undefined
      );
      res.status(200).json({ disputes });
    } catch (error) {
      res.status(400).json({ error: { message: (error as Error).message } });
    }
  });

  router.post('/disputes/:jobId/resolve', (req, res) => {
    try {
      const body = z
        .object({
          action: z.enum(['uphold_provider', 'slash_provider']),
          slashAmountAnmNanos: z.string().optional(),
          note: z.string().optional()
        })
        .parse(req.body ?? {});
      const job = service.resolveDispute(req.ctx.user!, {
        jobId: req.params.jobId,
        action: body.action,
        slashAmountAnmNanos: body.slashAmountAnmNanos,
        note: body.note
      });
      res.status(200).json({ job });
    } catch (error) {
      res.status(400).json({ error: { message: (error as Error).message } });
    }
  });

  router.post('/contract-disputes/:disputeId/resolve', (req, res) => {
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

  router.get('/finalization-queue', (req, res) => {
    try {
      const jobs = service
        .listContractJobs(req.ctx.user!)
        .filter((job) => job.state === 'result_submitted' || job.state === 'accepted');
      res.status(200).json({ jobs });
    } catch (error) {
      res.status(400).json({ error: { message: (error as Error).message } });
    }
  });

  router.post('/contract-jobs/:jobId/finalize', (req, res) => {
    try {
      const result = service.finalizeContractJob(req.ctx.user!, req.params.jobId);
      res.status(200).json(result);
    } catch (error) {
      res.status(400).json({ error: { message: (error as Error).message } });
    }
  });

  router.get('/treasury', (req, res) => {
    res.status(200).json({
      treasury: service.listTreasurySnapshot(),
      grants: service.listGrants(req.ctx.user!)
    });
  });

  router.post('/treasury/grants', (req, res) => {
    try {
      const body = z
        .object({
          projectId: z.string().min(3),
          amountAnmNanos: z.string().min(1),
          reason: z.string().min(4),
          expiresAt: z.string().datetime().optional()
        })
        .parse(req.body ?? {});
      const grant = service.allocateGrant(req.ctx.user!, body);
      res.status(201).json(grant);
    } catch (error) {
      res.status(400).json({ error: { message: (error as Error).message } });
    }
  });

  router.post('/treasury/deposit', (req, res) => {
    try {
      const body = z
        .object({
          amountAnmNanos: z.string().min(1),
          sourceTxHash: z.string().optional(),
          note: z.string().optional()
        })
        .parse(req.body ?? {});
      const payload = service.depositTreasury(req.ctx.user!, body);
      res.status(200).json(payload);
    } catch (error) {
      res.status(400).json({ error: { message: (error as Error).message } });
    }
  });

  router.get('/model-routing', (_req, res) => {
    res.status(200).json({ models: service.listModels() });
  });

  router.get('/feature-flags', (_req, res) => {
    res.status(200).json({ flags: service.listFeatureFlags() });
  });

  router.post('/feature-flags', (req, res) => {
    try {
      const body = z
        .object({
          key: z.string().min(4),
          enabled: z.boolean(),
          note: z.string().optional()
        })
        .parse(req.body ?? {});
      const flag = service.setFeatureFlag(req.ctx.user!, body);
      res.status(200).json({ flag });
    } catch (error) {
      res.status(400).json({ error: { message: (error as Error).message } });
    }
  });

  router.post('/pause', (req, res) => {
    try {
      const body = z
        .object({
          paused: z.boolean(),
          reason: z.string().optional()
        })
        .parse(req.body ?? {});
      service.pauseAll(req.ctx.user!, body.paused, body.reason);
      res.status(200).json({ paused: body.paused });
    } catch (error) {
      res.status(400).json({ error: { message: (error as Error).message } });
    }
  });

  router.get('/escrow', (req, res) => {
    const settlements = service.listSettlements(req.ctx.user!);
    res.status(200).json({ settlements });
  });

  router.get('/rewards', (req, res) => {
    const providers = service.listProviders(req.ctx.user!);
    res.status(200).json({
      rewards: providers.map((provider) => ({
        providerId: provider.id,
        rewardBalanceAnmNanos: service.providerRewardBalance(provider.id),
        state: provider.state,
        reputation: provider.reputation
      }))
    });
  });

  router.get('/audit-logs', (req, res) => {
    const logs = service.listAuditLogs(req.ctx.user!);
    res.status(200).json({ logs });
  });

  return router;
}
