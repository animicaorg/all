/**
 * Express HTTP Server Setup
 */

import express, { type Express } from "express";
import type { Pool } from "pg";
import type { Logger } from "pino";
import type { RedisClientType } from "redis";
import type { Config } from "../config.js";
import {
  createRateLimiter,
  createInMemoryRateLimiter,
  createBitGoAuthMiddleware,
  createAdminAuthMiddleware,
} from "./middleware/index.js";
import { setupWebhookRoutes } from "./routes/webhooks.js";
import { setupAdminRoutes } from "./routes/admin.js";

/**
 * Create and configure Express server
 */
export function createServer(
  pool: Pool,
  redis: RedisClientType | null,
  config: Config,
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

  // Webhook routes with rate limiting and auth
  const webhookRouter = express.Router();

  // Apply rate limiting
  if (redis) {
    webhookRouter.use(
      createRateLimiter(
        redis,
        {
          windowMs: 60 * 1000, // 1 minute
          maxRequests: config.WEBHOOK_RATE_LIMIT_PER_MINUTE,
          keyPrefix: "webhook:ratelimit",
        },
        logger
      )
    );
  } else {
    // Fallback to in-memory rate limiter
    webhookRouter.use(
      createInMemoryRateLimiter(
        {
          windowMs: 60 * 1000,
          maxRequests: config.WEBHOOK_RATE_LIMIT_PER_MINUTE,
          keyPrefix: "webhook:ratelimit",
        },
        logger
      )
    );
  }

  // Apply BitGo auth
  webhookRouter.use(
    createBitGoAuthMiddleware(
      {
        webhookSecret: config.BITGO_WEBHOOK_SECRET,
        replayWindowSeconds: config.WEBHOOK_REPLAY_WINDOW_SECONDS,
        requireAuth: !!config.BITGO_WEBHOOK_SECRET,
      },
      logger
    )
  );

  setupWebhookRoutes(webhookRouter, pool, logger);
  app.use(webhookRouter);

  // Admin routes with admin auth
  const adminRouter = express.Router();
  adminRouter.use(createAdminAuthMiddleware(config.ADMIN_KEY, logger));
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
