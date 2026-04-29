/**
 * Express HTTP Server Setup
 */

import express, { type Express } from "express";
import type { Pool } from "pg";
import type { Logger } from "@cex/observability";
import type { Config } from "../config.js";
import {
  createRateLimiter,
  createInMemoryRateLimiter,
  createWebhookVerificationMiddleware,
  createAdminAuthMiddleware,
} from "./middleware/index.js";
import { setupWebhookRoutes } from "./routes/webhooks.js";
import { setupAdminRoutes } from "./routes/admin.js";

// Rate limiting constants
const WEBHOOK_RATE_LIMIT_WINDOW_MS = 60 * 1000; // 1 minute

/**
 * Create and configure Express server
 */
export function createServer(
  pool: Pool,
  redis: {
    ping(): Promise<string>;
    incr(key: string): Promise<number>;
    pexpire(key: string, milliseconds: number): Promise<number>;
    pttl(key: string): Promise<number>;
  } | null,
  config: Config,
  logger: Logger
): Express {
  const app = express();

  // Parse JSON bodies
  app.use(express.json());

  // Request logging middleware
  app.use((req, res, next) => {
    const startTime = Date.now();
    const reqAny = req as typeof req & { id?: string };
    const headerRequestId = req.headers["x-request-id"];
    const requestIdFromHeader = Array.isArray(headerRequestId)
      ? headerRequestId[0]
      : headerRequestId;
    const requestId = reqAny.id || requestIdFromHeader || generateRequestId();
    reqAny.id = requestId;
    res.setHeader("X-Request-ID", requestId);

    // Create request logger
    const requestLogger = logger.child({
      request_id: requestId,
      method: req.method,
      path: req.path,
      ip: req.ip || req.socket.remoteAddress,
    });

    requestLogger.debug("Incoming request");

    // Log response on finish
    res.on("finish", () => {
      const duration = Date.now() - startTime;
      requestLogger.info(
        {
          status: res.statusCode,
          latency_ms: duration,
        },
        "Request completed"
      );
    });

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

  // Webhook routes with rate limiting and signature verification
  const webhookRouter = express.Router();

  // Apply rate limiting
  if (redis) {
    webhookRouter.use(
      createRateLimiter(
        redis,
        {
          windowMs: WEBHOOK_RATE_LIMIT_WINDOW_MS,
          maxRequests: config.WEBHOOK_RATE_LIMIT_PER_MINUTE,
          keyPrefix: "webhook_rl",
        },
        logger
      )
    );
  } else {
    // Fallback to in-memory rate limiter
    webhookRouter.use(
      createInMemoryRateLimiter(
        {
          windowMs: WEBHOOK_RATE_LIMIT_WINDOW_MS,
          maxRequests: config.WEBHOOK_RATE_LIMIT_PER_MINUTE,
          keyPrefix: "webhook_rl",
        },
        logger
      )
    );
  }

  // Apply webhook signature verification
  webhookRouter.use(
    createWebhookVerificationMiddleware(
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
  app.use((err: any, req: any, res: any, _next: any) => {
    logger.error({ error: err, path: req.path, method: req.method }, "Unhandled error");
    res.status(500).json({
      error: "Internal Server Error",
      message: err.message || "An unexpected error occurred",
    });
  });

  return app;
}

/**
 * Generate a unique request ID
 */
function generateRequestId(): string {
  return `req_${Date.now()}_${Math.random().toString(36).substring(2, 11)}`;
}
