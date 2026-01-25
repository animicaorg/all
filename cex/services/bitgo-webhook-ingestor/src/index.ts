import express from "express";
import { z } from "zod";
import {
  baseEnvSchema,
  connectNats,
  createLogger,
  createPgPool,
  createRedis,
  loadEnv
} from "@cex/common";

const env = loadEnv(
  baseEnvSchema.extend({
    SERVICE_NAME: z.string().default("bitgo-webhook-ingestor"),
    BITGO_WEBHOOK_SECRET: z.string().optional()
  })
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

  app.post("/webhook", (req, res) => {
    const signature = req.header("x-bitgo-signature") || "missing";
    if (!env.BITGO_WEBHOOK_SECRET) {
      logger.warn("BitGo webhook secret not configured");
    }
    logger.info({ signature, body: req.body }, "received BitGo webhook stub");
    res.status(202).json({ status: "accepted" });
  });

  const server = app.listen(env.PORT, () => {
    logger.info({ port: env.PORT }, "bitgo-webhook-ingestor listening");
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
  logger.error({ error }, "failed to start bitgo-webhook-ingestor");
  process.exit(1);
});
