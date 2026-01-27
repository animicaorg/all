/**
 * Matching Engine Service
 * 
 * Entry point for the matching engine service.
 * Currently configured to process commands for a single market.
 * For production, extend to handle multiple markets with worker pools.
 */

import express from "express";
import { createLogger, createPgPool, connectNats, createRedis } from "@cex/common";
import { loadEnv } from "./config.js";
import { MarketWorker } from "./workers/market_worker.js";
import { OutboxPublisher } from "./outbox/publisher.js";

const env = loadEnv();
const logger = createLogger(env.SERVICE_NAME, env.LOG_LEVEL);

const start = async () => {
  const app = express();
  app.use(express.json());

  const pgPool = createPgPool(env);
  const redis = createRedis(env);
  const nats = await connectNats(env);

  // Health check endpoint
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
    logger.info({ port: env.PORT }, "matching-engine listening");
  });

  // Start outbox publisher
  const outboxPublisher = new OutboxPublisher(pgPool, nats, logger);
  outboxPublisher.start().catch((error) => {
    logger.error({ error }, "Outbox publisher error");
  });

  // TODO: Initialize market workers based on configuration
  // For now, this is a stub. In production:
  // 1. Query active markets from DB
  // 2. Create MarketWorker for each market
  // 3. Subscribe to NATS command subjects per market
  // 4. Process commands via worker.placeLimitOrder(), etc.
  // 5. Implement worker pool management and failover

  logger.info("Matching engine initialized (stub - no active workers)");
  logger.info("To process orders, implement worker initialization and NATS subscriptions");

  const shutdown = async () => {
    logger.info("Shutting down matching engine");
    outboxPublisher.stop();
    await nats.drain();
    await pgPool.end();
    redis.disconnect();
    server.close();
  };

  process.on("SIGTERM", shutdown);
  process.on("SIGINT", shutdown);
};

start().catch((error) => {
  logger.error({ error }, "Failed to start matching-engine");
  process.exit(1);
});
