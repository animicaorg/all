import { Router } from 'express';
import { z } from 'zod';
import type { AccountUser } from '@animica/aicf-shared';
import { providerDaemonAuth, sessionAuth } from '../../middleware/auth.js';
import type { PlatformService } from '../../services/platformService.js';

function requireProvider(service: PlatformService, user: AccountUser) {
  const provider = service.listProviders(user).find((candidate) => candidate.userId === user.id);
  if (!provider) {
    throw new Error('Provider profile not found. Register first.');
  }
  return provider;
}

export function createProviderRouter(service: PlatformService) {
  const router = Router();

  router.post('/register', sessionAuth(service), (req, res) => {
    try {
      const body = z
        .object({
          walletAddress: z.string().min(8),
          signature: z.string().min(8),
          daemonPublicKey: z.string().min(8)
        })
        .parse(req.body ?? {});
      const result = service.registerProvider(req.ctx.user!, body);
      const contractCalls = service.createProviderContractCalls(result.provider);
      res.status(201).json({
        provider: result.provider,
        daemonToken: result.daemonToken,
        contractCalls
      });
    } catch (error) {
      res.status(400).json({ error: { message: (error as Error).message } });
    }
  });

  router.get('/profile', sessionAuth(service), (req, res) => {
    try {
      const provider = requireProvider(service, req.ctx.user!);
      res.status(200).json({
        provider,
        rewardBalanceAnmNanos: service.providerRewardBalance(provider.id)
      });
    } catch (error) {
      res.status(404).json({ error: { message: (error as Error).message } });
    }
  });

  router.post('/stake', sessionAuth(service), (req, res) => {
    try {
      const body = z.object({ amountAnmNanos: z.string().min(1) }).parse(req.body ?? {});
      const provider = requireProvider(service, req.ctx.user!);
      const updated = service.providerStake(provider, body.amountAnmNanos);
      res.status(200).json({ provider: updated });
    } catch (error) {
      res.status(400).json({ error: { message: (error as Error).message } });
    }
  });

  router.post('/unstake', sessionAuth(service), (req, res) => {
    try {
      const body = z.object({ amountAnmNanos: z.string().min(1) }).parse(req.body ?? {});
      const provider = requireProvider(service, req.ctx.user!);
      const updated = service.providerUnstake(provider, body.amountAnmNanos);
      res.status(200).json({ provider: updated });
    } catch (error) {
      res.status(400).json({ error: { message: (error as Error).message } });
    }
  });

  router.post('/nodes', sessionAuth(service), (req, res) => {
    try {
      const provider = requireProvider(service, req.ctx.user!);
      const body = z
        .object({
          metadata: z.object({
            name: z.string().min(2),
            machineType: z.string().min(2),
            os: z.string().min(2),
            labels: z.array(z.string()).default([])
          }),
          capabilities: z.object({
            runtime: z.enum(['llm', 'embedding', 'training', 'agent', 'custom']),
            gpus: z.number().int().min(0),
            gpuMemoryGb: z.number().int().min(0),
            cpus: z.number().int().positive(),
            ramGb: z.number().int().positive(),
            region: z.string().min(2),
            modelFamilies: z.array(z.string()).default([])
          }),
          benchmark: z.object({
            llmTokensPerSecond: z.number().nonnegative(),
            embeddingVectorsPerSecond: z.number().nonnegative(),
            trainingSamplesPerSecond: z.number().nonnegative(),
            score: z.number().min(0).max(100)
          })
        })
        .parse(req.body ?? {});
      const node = service.registerProviderNode(provider, body);
      res.status(201).json({ node });
    } catch (error) {
      res.status(400).json({ error: { message: (error as Error).message } });
    }
  });

  router.get('/nodes', sessionAuth(service), (req, res) => {
    try {
      const provider = requireProvider(service, req.ctx.user!);
      const nodes = service.listProviderNodes(req.ctx.user!, provider.id);
      res.status(200).json({ nodes });
    } catch (error) {
      res.status(400).json({ error: { message: (error as Error).message } });
    }
  });

  router.get('/jobs', sessionAuth(service), (req, res) => {
    try {
      const provider = requireProvider(service, req.ctx.user!);
      const jobs = service.listProviderJobs(provider);
      res.status(200).json({ jobs });
    } catch (error) {
      res.status(400).json({ error: { message: (error as Error).message } });
    }
  });

  router.get('/contract-jobs', sessionAuth(service), (req, res) => {
    try {
      const provider = requireProvider(service, req.ctx.user!);
      const jobs = service.listProviderContractJobs(provider);
      res.status(200).json({ jobs });
    } catch (error) {
      res.status(400).json({ error: { message: (error as Error).message } });
    }
  });

  router.post('/rewards/claim', sessionAuth(service), (req, res) => {
    try {
      const provider = requireProvider(service, req.ctx.user!);
      const claim = service.providerClaimRewards(provider);
      res.status(200).json(claim);
    } catch (error) {
      res.status(400).json({ error: { message: (error as Error).message } });
    }
  });

  const daemon = Router();
  daemon.use(providerDaemonAuth(service));

  daemon.post('/jobs/claim', (req, res) => {
    try {
      const body = z
        .object({
          nodeId: z.string().min(3),
          limit: z.number().int().positive().max(100).default(10)
        })
        .parse(req.body ?? {});
      const jobs = service.providerClaimJobs(req.ctx.provider!, body.nodeId, body.limit);
      res.status(200).json({ jobs });
    } catch (error) {
      res.status(400).json({ error: { message: (error as Error).message } });
    }
  });

  daemon.post('/nodes/:nodeId/heartbeat', (req, res) => {
    try {
      const body = z
        .object({
          currentLoad: z.number().min(0).max(100),
          queueDepth: z.number().int().min(0),
          state: z.enum(['active', 'inactive', 'quarantined']).optional()
        })
        .parse(req.body ?? {});
      const node = service.heartbeatProviderNode(req.ctx.provider!, req.params.nodeId, body);
      res.status(200).json({ node });
    } catch (error) {
      res.status(400).json({ error: { message: (error as Error).message } });
    }
  });

  daemon.post('/jobs/:jobId/result', (req, res) => {
    try {
      const body = z
        .object({
          nodeId: z.string().min(3),
          output: z.record(z.unknown()),
          usage: z
            .object({
              inputTokens: z.number().int().nonnegative().optional(),
              outputTokens: z.number().int().nonnegative().optional(),
              embeddingVectors: z.number().int().nonnegative().optional(),
              latencyMs: z.number().int().positive().optional(),
              bytesIn: z.number().int().nonnegative().optional(),
              bytesOut: z.number().int().nonnegative().optional()
            })
            .optional()
        })
        .parse(req.body ?? {});
      const job = service.providerSubmitResult(req.ctx.provider!, body.nodeId, req.params.jobId, {
        output: body.output,
        usage: body.usage
      });
      res.status(200).json({ job });
    } catch (error) {
      res.status(400).json({ error: { message: (error as Error).message } });
    }
  });

  daemon.post('/jobs/:jobId/fail', (req, res) => {
    try {
      const body = z
        .object({
          nodeId: z.string().min(3),
          reason: z.string().min(4)
        })
        .parse(req.body ?? {});
      const job = service.providerFailJob(req.ctx.provider!, body.nodeId, req.params.jobId, body.reason);
      res.status(200).json({ job });
    } catch (error) {
      res.status(400).json({ error: { message: (error as Error).message } });
    }
  });

  daemon.post('/contract-jobs/claim', (req, res) => {
    try {
      const body = z
        .object({
          nodeId: z.string().min(3),
          limit: z.number().int().positive().max(100).default(10)
        })
        .parse(req.body ?? {});
      const jobs = service.providerClaimContractJobs(req.ctx.provider!, body.nodeId, body.limit);
      res.status(200).json({ jobs });
    } catch (error) {
      res.status(400).json({ error: { message: (error as Error).message } });
    }
  });

  daemon.post('/contract-jobs/:jobId/commitment', (req, res) => {
    try {
      const body = z
        .object({
          nodeId: z.string().min(3),
          resultHash: z.string().min(8),
          resultRef: z.string().optional(),
          signature: z.string().min(8),
          modelRuntime: z.string().min(2),
          usage: z.object({
            inputTokens: z.number().int().nonnegative(),
            outputTokens: z.number().int().nonnegative(),
            embeddingVectors: z.number().int().nonnegative(),
            latencyMs: z.number().int().positive(),
            bytesIn: z.number().int().nonnegative(),
            bytesOut: z.number().int().nonnegative()
          }),
          toolTraceRef: z.string().optional(),
          verifierRef: z.string().optional(),
          quorumGroup: z.string().optional()
        })
        .parse(req.body ?? {});
      const payload = service.providerSubmitContractResultCommitment(req.ctx.provider!, req.params.jobId, body);
      res.status(200).json(payload);
    } catch (error) {
      res.status(400).json({ error: { message: (error as Error).message } });
    }
  });

  daemon.post('/contract-jobs/:jobId/reference', (req, res) => {
    try {
      const body = z.object({ resultRef: z.string().min(4) }).parse(req.body ?? {});
      const job = service.submitContractResultReference(req.ctx.provider!, req.params.jobId, body);
      res.status(200).json({ job });
    } catch (error) {
      res.status(400).json({ error: { message: (error as Error).message } });
    }
  });

  router.use('/daemon', daemon);

  return router;
}
