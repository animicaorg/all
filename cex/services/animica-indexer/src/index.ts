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
    SERVICE_NAME: z.string().default("animica-indexer"),
    ANIMICA_RPC_URL: z.string().url().default("http://127.0.0.1:8545/rpc")
  })
);

const logger = createLogger(env.SERVICE_NAME, env.LOG_LEVEL);

const pingAnimica = async () => {
  try {
    const response = await fetch(env.ANIMICA_RPC_URL, {
      method: "POST",
      headers: {
        "Content-Type": "application/json"
      },
      body: JSON.stringify({ jsonrpc: "2.0", id: 1, method: "ping", params: [] })
    });
    logger.info({ status: response.status }, "Animica RPC ping response");
  } catch (error) {
    logger.warn({ error }, "Animica RPC not reachable");
  }
};

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

  const server = app.listen(env.PORT, "0.0.0.0", () => {
    logger.info({ port: env.PORT }, "animica-indexer listening");
  });

  await pingAnimica();

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
  logger.error({ error }, "failed to start animica-indexer");
  process.exit(1);
});
