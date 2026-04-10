/**
 * Express HTTP Server
 * Main entry point for the REST API
 */

import express, { type Express } from 'express';
import helmet from 'helmet';
import type { PrismaClient } from '@prisma/client';
import type { RedisClientType } from 'redis';
import type { Config } from '../config.js';
import type { Logger } from '../utils/logger.js';
import {
  requestIdMiddleware,
  createCorsMiddleware,
  createErrorHandler,
  createRateLimiters,
  createApiKeyAuthMiddleware,
} from './middleware/index.js';
import {
  createPublicMarketsRouter,
  createPublicOrderbookRouter,
  createPublicTradesRouter,
  createPublicTickersRouter,
  createPublicCandlesRouter,
  createPrivateAccountsRouter,
  createPrivateOrdersRouter,
  createPrivateTransfersRouter,
  createAuthRouter,
} from './routes/index.js';
import {
  MarketDataCache,
  MatchingEngineClient,
  LedgerClient,
  UsersClient,
  DepositsClient,
  WithdrawalsClient,
} from '../services/index.js';
import { LedgerService } from '../services/ledger.js';

export interface ServerDependencies {
  prisma: PrismaClient;
  redis: RedisClientType | null;
  config: Config;
  logger: Logger;
}

/**
 * Create and configure Express application
 */
export function createApp(deps: ServerDependencies): Express {
  const { prisma, redis, config, logger } = deps;
  const app = express();

  // Initialize services
  const ledgerService = new LedgerService(prisma);
  const marketDataCache = new MarketDataCache(config, logger);
  const matchingEngineClient = new MatchingEngineClient(config, logger);
  const ledgerClient = new LedgerClient(prisma, ledgerService, logger);
  const usersClient = new UsersClient(prisma, logger);
  const depositsClient = new DepositsClient(prisma, config, logger);
  const withdrawalsClient = new WithdrawalsClient(prisma, config, logger);

  const services = {
    marketDataCache,
    matchingEngineClient,
    ledgerClient,
    usersClient,
    depositsClient,
    withdrawalsClient,
  };

  // Security middleware
  app.use(
    helmet({
      contentSecurityPolicy: {
        directives: {
          defaultSrc: ["'self'"],
          styleSrc: ["'self'", "'unsafe-inline'"],
        },
      },
      hsts: {
        maxAge: 31536000,
        includeSubDomains: true,
        preload: true,
      },
    })
  );

  // Basic middleware
  app.use(express.json({ limit: '1mb' }));
  app.use(express.urlencoded({ extended: true, limit: '1mb' }));
  app.use(requestIdMiddleware);
  app.use(createCorsMiddleware(config));

  // Request logging
  app.use((req, _res, next) => {
    logger.debug(
      {
        method: req.method,
        path: req.path,
        query: req.query,
        ip: req.ip,
      },
      'HTTP request'
    );
    next();
  });

  // Rate limiters
  const rateLimiters = createRateLimiters(redis, config, logger);

  // Health check (no auth, no rate limit)
  app.get('/healthz', async (_req, res) => {
    try {
      const pgOk = await prisma.$queryRaw`SELECT 1`
        .then(() => true)
        .catch(() => false);

      const redisOk = redis
        ? await redis
            .ping()
            .then(() => true)
            .catch(() => false)
        : null;

      res.json({
        status: pgOk ? 'ok' : 'degraded',
        service: config.SERVICE_NAME,
        version: '0.1.0',
        postgres: pgOk,
        redis: redisOk,
        timestamp: new Date().toISOString(),
      });
    } catch (error) {
      logger.error({ error }, 'Health check failed');
      res.status(503).json({
        status: 'error',
        service: config.SERVICE_NAME,
        error: 'Health check failed',
      });
    }
  });

  // Public API routes (with IP-based rate limiting)
  const publicRouter = express.Router();
  publicRouter.use(rateLimiters.public);

  publicRouter.use('/markets', createPublicMarketsRouter(prisma, logger));
  publicRouter.use(
    '/orderbook',
    createPublicOrderbookRouter(marketDataCache, config, logger)
  );
  publicRouter.use('/trades', createPublicTradesRouter(prisma, config, logger));
  publicRouter.use('/tickers', createPublicTickersRouter(marketDataCache, logger));
  publicRouter.use('/candles', createPublicCandlesRouter(prisma, config, logger));

  app.use('/api/v1', publicRouter);

  // Private API routes (with API key auth and key-based rate limiting)
  const privateRouter = express.Router();
  privateRouter.use(createApiKeyAuthMiddleware(prisma, redis, config, logger));
  privateRouter.use(rateLimiters.private);

  privateRouter.use(
    '/account',
    createPrivateAccountsRouter(usersClient, ledgerClient, logger)
  );
  privateRouter.use(
    '/balances',
    createPrivateAccountsRouter(usersClient, ledgerClient, logger)
  );
  privateRouter.use(
    '/orders',
    createPrivateOrdersRouter(prisma, services, config, logger)
  );
  privateRouter.use(
    '/transfers',
    createPrivateTransfersRouter(prisma, services, logger)
  );
  privateRouter.use('/withdrawals', createPrivateTransfersRouter(prisma, services, logger));
  privateRouter.use('/auth', createAuthRouter(prisma, config, logger));

  app.use('/api/v1', privateRouter);

  // 404 handler
  app.use((_req, res) => {
    res.status(404).json({
      error: {
        code: 'NOT_FOUND',
        message: 'Endpoint not found',
      },
    });
  });

  // Error handler (must be last)
  app.use(createErrorHandler(logger));

  return app;
}

/**
 * Start HTTP server
 */
export async function startServer(deps: ServerDependencies): Promise<void> {
  const { config, logger } = deps;
  const app = createApp(deps);

  const server = app.listen(config.HTTP_PORT, config.HTTP_HOST, () => {
    logger.info(
      {
        port: config.HTTP_PORT,
        host: config.HTTP_HOST,
        env: config.NODE_ENV,
      },
      'HTTP server listening'
    );
  });

  // Graceful shutdown
  const shutdown = async () => {
    logger.info('Shutting down HTTP server...');
    
    server.close(() => {
      logger.info('HTTP server closed');
    });

    if (deps.redis) {
      await deps.redis.quit();
      logger.info('Redis disconnected');
    }

    await deps.prisma.$disconnect();
    logger.info('Prisma disconnected');

    process.exit(0);
  };

  process.on('SIGTERM', shutdown);
  process.on('SIGINT', shutdown);

  // Handle uncaught errors
  process.on('uncaughtException', (error) => {
    logger.fatal({ error }, 'Uncaught exception');
    process.exit(1);
  });

  process.on('unhandledRejection', (reason) => {
    logger.fatal({ reason }, 'Unhandled rejection');
    process.exit(1);
  });
}
