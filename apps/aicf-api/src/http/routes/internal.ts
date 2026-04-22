import { Router } from 'express';
import { internalSecretAuth } from '../../middleware/auth.js';
import type { AppConfig } from '../../config.js';
import type { PlatformService } from '../../services/platformService.js';

export function createInternalRouter(service: PlatformService, config: AppConfig) {
  const router = Router();

  router.get('/health', (_req, res) => {
    res.status(200).json(service.health());
  });

  router.use(internalSecretAuth(config.AICF_API_INTERNAL_SECRET));

  router.post('/scheduler/tick', (_req, res) => {
    const result = service.schedulerTick();
    res.status(200).json(result);
  });

  router.post('/jobs/first-party-fallback', (req, res) => {
    const limitRaw = req.query.limit ? Number(req.query.limit) : 5;
    const limit = Number.isFinite(limitRaw) ? Math.max(1, Math.min(100, Math.trunc(limitRaw))) : 5;
    const result = service.runFirstPartyFallback(limit);
    res.status(200).json(result);
  });

  router.get('/chain-events/watch', (req, res) => {
    const limitRaw = req.query.limit ? Number(req.query.limit) : 50;
    const limit = Number.isFinite(limitRaw) ? Math.max(1, Math.min(500, Math.trunc(limitRaw))) : 50;
    const events = service.watchChainEvents(limit);
    res.status(200).json({ events });
  });

  router.post('/chain-events/ingest', (req, res) => {
    try {
      const body = req.body ?? {};
      const event = service.ingestObservedChainEvent({
        eventType: String(body.eventType) as never,
        contractAddress: String(body.contractAddress),
        onchainJobId: body.onchainJobId ? String(body.onchainJobId) : undefined,
        onchainTaskId: body.onchainTaskId ? String(body.onchainTaskId) : undefined,
        payload: typeof body.payload === 'object' && body.payload ? body.payload : {}
      });
      res.status(201).json({ event });
    } catch (error) {
      res.status(400).json({ error: { message: (error as Error).message } });
    }
  });

  router.post('/contract-jobs/scheduler/tick', (req, res) => {
    const limitRaw = req.query.limit ? Number(req.query.limit) : 50;
    const limit = Number.isFinite(limitRaw) ? Math.max(1, Math.min(500, Math.trunc(limitRaw))) : 50;
    const result = service.scheduleContractJobs(limit);
    res.status(200).json(result);
  });

  router.post('/contract-jobs/result-submitter/tick', (req, res) => {
    const limitRaw = req.query.limit ? Number(req.query.limit) : 30;
    const limit = Number.isFinite(limitRaw) ? Math.max(1, Math.min(500, Math.trunc(limitRaw))) : 30;
    const result = service.contractResultSubmitterTick(limit);
    res.status(200).json(result);
  });

  router.post('/contract-jobs/finalization/tick', (req, res) => {
    const limitRaw = req.query.limit ? Number(req.query.limit) : 20;
    const limit = Number.isFinite(limitRaw) ? Math.max(1, Math.min(500, Math.trunc(limitRaw))) : 20;
    const result = service.contractFinalizationTick(limit);
    res.status(200).json(result);
  });

  router.get('/treasury/snapshot', (_req, res) => {
    res.status(200).json({ treasury: service.listTreasurySnapshot() });
  });

  return router;
}
