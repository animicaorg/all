/**
 * Express HTTP Server Setup
 */

import express, { type Express } from "express";
import type { Pool } from "pg";
import type { Logger } from "pino";
import type { Config } from "../config.js";
import type { BitgoConfigStore } from "../bitgo/config.js";
import {
  createAuthMiddleware,
  createAdminAuthMiddleware,
  createRateLimiter,
  createInMemoryRateLimiter,
  createIdempotencyMiddleware,
} from "./middleware/index.js";
import {
  setupWithdrawalRoutes,
  setupBitGoWebhookRoutes,
  setupAdminRoutes,
} from "./routes/index.js";

interface RedisClient {
  ping(): Promise<string>;
  incr(key: string): Promise<number>;
  pexpire(key: string, milliseconds: number): Promise<number>;
  pttl(key: string): Promise<number>;
}

/**
 * Create and configure Express server
 */
export function createServer(
  pool: Pool,
  redis: RedisClient | null,
  config: Config,
  bitgoConfigStore: BitgoConfigStore,
  logger: Logger
): Express {
  const app = express();

  // Parse JSON bodies
  app.use(express.json());

  // Request logging middleware
  app.use((req, _res, next) => {
    logger.debug(
      {
        method: req.method,
        path: req.path,
        ip: req.ip,
      },
      "HTTP request"
    );
    next();
  });

  // Health check endpoint (no auth required)
  app.get("/healthz", async (_req, res) => {
    try {
      const pgOk = await pool
        .query("SELECT 1")
        .then(() => true)
        .catch(() => false);

      const redisOk = redis
        ? await redis.ping().then(() => true).catch(() => false)
        : true;

      const healthy = pgOk && redisOk;

      res.status(healthy ? 200 : 503).json({
        status: healthy ? "ok" : "unhealthy",
        service: config.SERVICE_NAME,
        postgres: pgOk,
        redis: redisOk,
      });
    } catch (error) {
      logger.error({ error }, "Health check error");
      res.status(503).json({
        status: "unhealthy",
        service: config.SERVICE_NAME,
      });
    }
  });

  // Webhook routes (signature verification, no user auth)
  const webhookRouter = express.Router();
  setupBitGoWebhookRoutes(webhookRouter, pool, bitgoConfigStore, logger);
  app.use(webhookRouter);

  // User withdrawal routes (authentication + rate limiting + idempotency)
  const withdrawalRouter = express.Router();

  // Apply authentication
  withdrawalRouter.use(createAuthMiddleware(logger));

  // Apply rate limiting
  if (redis) {
    withdrawalRouter.use(
      "/withdrawals",
      createRateLimiter(
        redis,
        {
          windowMs: 60 * 60 * 1000, // 1 hour
          maxRequests: config.WITHDRAWAL_REQUEST_RATE_LIMIT,
          keyPrefix: "withdrawal:ratelimit",
        },
        logger
      )
    );
  } else {
    withdrawalRouter.use(
      "/withdrawals",
      createInMemoryRateLimiter(
        {
          windowMs: 60 * 60 * 1000,
          maxRequests: config.WITHDRAWAL_REQUEST_RATE_LIMIT,
          keyPrefix: "withdrawal:ratelimit",
        },
        logger
      )
    );
  }

  // Apply idempotency for POST requests
  withdrawalRouter.post(
    "/withdrawals",
    createIdempotencyMiddleware(pool, logger)
  );

  setupWithdrawalRoutes(withdrawalRouter, pool, logger);
  app.use(withdrawalRouter);

  // Admin routes (admin auth)
  const adminRouter = express.Router();
  adminRouter.use(createAdminAuthMiddleware(config.ADMIN_API_KEY, logger));
  setupAdminRoutes(adminRouter, pool, logger);
  app.use(adminRouter);

  // 404 handler
  app.use((_req, res) => {
    res.status(404).json({
      error: "Not Found",
      message: "Endpoint not found",
    });
  });

  // Error handler
  app.use((err: any, _req: any, res: any, _next: any) => {
    logger.error({ error: err }, "Unhandled error");
    res.status(500).json({
      error: "Internal Server Error",
      message: err.message || "An unexpected error occurred",
    });
  });

  return app;
}
