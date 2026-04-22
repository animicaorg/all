import cors from 'cors';
import express from 'express';
import helmet from 'helmet';
import rateLimit from 'express-rate-limit';
import type { AppConfig } from '../config.js';
import type { AppLogger } from '../logger.js';
import { errorHandlerMiddleware } from '../middleware/errorHandler.js';
import { requestContextMiddleware } from '../middleware/requestContext.js';
import type { PlatformService } from '../services/platformService.js';
import { createAdminRouter } from './routes/admin.js';
import { createAuthRouter } from './routes/auth.js';
import { createAgentTasksRouter } from './routes/agentTasks.js';
import { createContractJobsRouter } from './routes/contractJobs.js';
import { createContractsRouter } from './routes/contracts.js';
import { createDisputesRouter } from './routes/disputes.js';
import { createDownloadsRouter } from './routes/downloads.js';
import { createInternalRouter } from './routes/internal.js';
import { createJobsRouter } from './routes/jobs.js';
import { createModelsRouter } from './routes/models.js';
import { createOpenAiRouter } from './routes/openai.js';
import { createProjectsRouter } from './routes/projects.js';
import { createProviderRouter } from './routes/provider.js';
import { createStatusRouter } from './routes/status.js';
import { createUsageRouter } from './routes/usage.js';
import { createWalletRouter } from './routes/wallet.js';

export function createApp(service: PlatformService, config: AppConfig, logger: AppLogger) {
  const app = express();

  app.use(helmet());
  app.use(
    cors({
      origin: true,
      credentials: true
    })
  );
  app.use(
    rateLimit({
      windowMs: config.AICF_RATE_LIMIT_WINDOW_MS,
      max: config.AICF_RATE_LIMIT_MAX,
      standardHeaders: true,
      legacyHeaders: false
    })
  );
  app.use((req, _res, next) => {
    logger.info({ method: req.method, url: req.url }, 'http_request');
    next();
  });
  app.use(express.json({ limit: '2mb' }));
  app.use(requestContextMiddleware);

  app.get('/', (_req, res) => {
    res.status(200).json({
      service: 'aicf-api',
      network: 'animica',
      chainId: config.AICF_CHAIN_ID,
      docs: '/docs',
      openai_compatible: ['/v1/chat/completions', '/v1/embeddings']
    });
  });

  app.get('/docs', (_req, res) => {
    res.status(200).json({
      openapi: '/openapi.json',
      quickstart: 'See docs/aicf-cloud/quickstart.md'
    });
  });

  app.get('/openapi.json', (_req, res) => {
    res.status(200).json({
      title: 'AICF API',
      version: '0.1.0',
      description: 'ANM-native decentralized AI compute API'
    });
  });

  app.use('/status', createStatusRouter(service));
  app.use('/downloads', createDownloadsRouter(config));
  app.use('/auth', createAuthRouter(service));
  app.use('/models', createModelsRouter(service));
  app.use('/projects', createProjectsRouter(service));
  app.use('/wallet', createWalletRouter(service));
  app.use('/jobs', createJobsRouter(service));
  app.use('/contract-jobs', createContractJobsRouter(service));
  app.use('/contracts', createContractsRouter(service));
  app.use('/agent-tasks', createAgentTasksRouter(service));
  app.use('/disputes', createDisputesRouter(service));
  app.use('/usage', createUsageRouter(service));
  app.use('/provider', createProviderRouter(service));
  app.use('/admin', createAdminRouter(service));
  app.use('/v1', createOpenAiRouter(service));
  app.use('/internal', createInternalRouter(service, config));

  app.use(errorHandlerMiddleware);

  return app;
}
