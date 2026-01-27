import express from "express";
import { z } from "zod";
import {
  baseEnvSchema,
  connectNats,
  createLogger,
  createPgPool,
  createRedis,
  extendWithHostPort,
  loadEnv
} from "@cex/common";

const env = loadEnv(
  extendWithHostPort(
    baseEnvSchema.extend({
      SERVICE_NAME: z.string().default("admin-service")
    }),
    { defaultPort: 4000 }
  )
);

const logger = createLogger(env.SERVICE_NAME, env.LOG_LEVEL);

const start = async () => {
  const app = express();
  app.use(express.json());

  const pgPool = createPgPool(env);
  const redis = createRedis(env);
  const nats = await connectNats(env);

  app.get("/healthz", async (_req, res) => {
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
  });

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
      "admin-service listening"
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
    logger.error({ error }, "admin-service failed to start");
    process.exit(1);
  });

  const shutdown = async () => {
    await nats.drain();
    await pgPool.end();
    redis.disconnect();
    server.close();
  };

  process.on("SIGTERM", shutdown);
  process.on("SIGINT", shutdown);
};

start().catch((error) => {
  logger.error({ error }, "failed to start admin-service");
  process.exit(1);
});
