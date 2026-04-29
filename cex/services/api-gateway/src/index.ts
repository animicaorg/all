import express from "express";
import cors from "cors";
import { z } from "zod";
import { OpenAPIRegistry, OpenApiGeneratorV3 } from "@asteasolutions/zod-to-openapi";
import {
  baseEnvSchema,
  connectNats,
  createLogger,
  createPgPool,
  createRedis,
  extendWithHostPort,
  loadEnv,
} from "@cex/common";
import metaRouter from "./routes/meta.js";
import { createAuthProxyRouter } from "./routes/auth.js";
import { createMarketsRouter } from "./routes/markets.js";
import { createOrdersRouter } from "./routes/orders.js";
import { createStatsRouter } from "./routes/stats.js";
import { createWebSocketServer } from "./websocket.js";

const env = loadEnv(
  extendWithHostPort(
    baseEnvSchema.extend({
      SERVICE_NAME: z.string().default("api-gateway"),
      AUTH_SERVICE_URL: z
        .string()
        .url()
        .default(`http://auth-service:${process.env.AUTH_SERVICE_PORT ?? "3100"}`)
    }),
    { defaultPort: 3000 }
  )
);

const logger = createLogger(env.SERVICE_NAME, env.LOG_LEVEL);

const start = async () => {
  const app = express();
  
  // Middleware
  // ⚠️ SECURITY WARNING: CORS is configured for development only!
  // In production, replace `origin: true` with a whitelist of allowed domains
  app.use(cors({
    origin: process.env.NODE_ENV === 'production' 
      ? process.env.ALLOWED_ORIGINS?.split(',') || false
      : true,
    credentials: true,
  }));
  app.use(express.json());

  const registry = new OpenAPIRegistry();
  const healthResponseSchema = z.object({
    status: z.string(),
    service: z.string(),
    postgres: z.boolean(),
    redis: z.boolean(),
    nats: z.string()
  });

  registry.registerPath({
    method: "get",
    path: "/healthz",
    responses: {
      200: {
        description: "Health check response",
        content: {
          "application/json": {
            schema: healthResponseSchema
          }
        }
      }
    }
  });

  const pgPool = createPgPool(env);
  const redis = createRedis(env);
  const nats = await connectNats(env);

  const healthHandler = async (_req: any, res: express.Response) => {
    const pgOk = await pgPool
      .query("SELECT 1")
      .then(() => true)
      .catch(() => false);
    const redisOk = await redis
      .ping()
      .then(() => true)
      .catch(() => false);
    res.json({
      status: "ok",
      service: env.SERVICE_NAME,
      postgres: pgOk,
      redis: redisOk,
      nats: nats.isClosed() ? "closed" : "open"
    });
  };

  // Support both /health and /healthz because existing infra probes and runbooks use /health.
  app.get("/health", healthHandler);
  app.get("/healthz", healthHandler);

  // OpenAPI documentation
  app.get("/openapi.json", (_req: any, res) => {
    const generator = new OpenApiGeneratorV3(registry.definitions);
    res.json(
      generator.generateDocument({
        openapi: "3.0.0",
        info: {
          title: "Animica CEX API Gateway",
          version: "0.1.0"
        }
      })
    );
  });

  // Routes
  const authProxyRouter = createAuthProxyRouter({ authServiceUrl: env.AUTH_SERVICE_URL });
  const marketsRouter = createMarketsRouter(pgPool);
  const ordersRouter = createOrdersRouter(pgPool, nats);
  const statsRouter = createStatsRouter(pgPool);

  app.use(authProxyRouter);
  app.use(metaRouter);
  app.use(marketsRouter);
  app.use(ordersRouter);
  app.use(statsRouter);

  // Preserve /api/v1 compatibility expected by web clients.
  app.use("/api/v1", authProxyRouter);
  app.use("/api/v1", metaRouter);
  app.use("/api/v1", marketsRouter);
  app.use("/api/v1", ordersRouter);
  app.use("/api/v1", statsRouter);

  // Start HTTP server
  const server = app.listen(env.PORT, env.HOST, () => {
    const address = server.address();
    const actualPort = typeof address === "string" ? env.PORT : address?.port ?? env.PORT;
    const actualHost = typeof address === "string" ? env.HOST : address?.address ?? env.HOST;
    const version = process.env.APP_VERSION ?? process.env.npm_package_version;
    logger.info(
      {
        service: env.SERVICE_NAME,
        host: actualHost,
        port: actualPort,
        env: process.env.NODE_ENV ?? "unknown",
        ...(version ? { version } : {})
      },
      "api-gateway listening"
    );
  });
  
  server.on("error", (error) => {
    const code = (error as NodeJS.ErrnoException).code;
    if (code === "EADDRINUSE") {
      logger.error(
        {
          service: env.SERVICE_NAME,
          host: env.HOST,
          port: env.PORT,
          error
        },
        "Port is already in use. Set PORT to a different value or free the port."
      );
      logger.error(
        `Check usage: lsof -nP -iTCP:${env.PORT} -sTCP:LISTEN || ss -ltnp | grep ":${env.PORT}"`
      );
      process.exit(1);
    }
    logger.error({ error }, "api-gateway failed to start");
    process.exit(1);
  });

  // Start WebSocket server
  const wss = createWebSocketServer(server, pgPool, nats);
  logger.info({ path: "/ws" }, "WebSocket server started");

  // Graceful shutdown
  const shutdown = async () => {
    logger.info("Shutting down gracefully...");
    wss.close();
    await nats.drain();
    await pgPool.end();
    redis.disconnect();
    server.close();
  };

  process.on("SIGTERM", shutdown);
  process.on("SIGINT", shutdown);
};

start().catch((error) => {
  logger.error({ error }, "failed to start api-gateway");
  process.exit(1);
});
