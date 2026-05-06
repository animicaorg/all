/**
 * Withdrawals Service
 * 
 * Main entry point - sets up HTTP server and background jobs
 */

import { createLogger, createPgPool, createRedis } from "@cex/common";
import { loadConfig } from "./config.js";
import { createServer } from "./http/server.js";
import { createBitGoClient } from "./bitgo/client.js";
import { BitgoConfigStore } from "./bitgo/config.js";
import { OutboxWorker } from "./outbox/worker.js";
import { PollPendingJob, ReconciliationJob } from "./jobs/index.js";

const config = loadConfig();
const logger = createLogger(config.SERVICE_NAME, config.LOG_LEVEL);

async function start() {
  logger.info(
    {
      config: {
        ...config,
        BITGO_ACCESS_TOKEN: config.BITGO_ACCESS_TOKEN ? "***" : undefined,
        BITGO_WEBHOOK_SECRET: config.BITGO_WEBHOOK_SECRET ? "***" : undefined,
        BITGO_WALLET_PASSPHRASE: config.BITGO_WALLET_PASSPHRASE ? "***" : undefined,
        ADMIN_API_KEY: config.ADMIN_API_KEY ? "***" : undefined,
      },
    },
    "Starting withdrawals service"
  );

  // Initialize connections
  const pool = createPgPool(config as any);
  const redis = createRedis(config as any);

  // Fail fast if core dependencies are not reachable.
  try {
    await pool.query("SELECT 1");
  } catch (error) {
    logger.error(
      {
        err: error,
        dbHost: config.DB_HOST,
        dbPort: config.DB_PORT,
        dbName: config.DB_NAME,
      },
      "Failed to connect to PostgreSQL"
    );
    throw error;
  }

  try {
    await redis.ping();
  } catch (error) {
    logger.error(
      {
        err: error,
        redisUrl: config.REDIS_URL,
      },
      "Failed to connect to Redis"
    );
    throw error;
  }

  logger.info("Database and Redis connections established");

  // Create BitGo client
  const bitgoConfigStore = new BitgoConfigStore(pool, config, logger);
  const bitgoClient = createBitGoClient(bitgoConfigStore, logger);

  // Create HTTP server
  const app = createServer(pool, redis, config, bitgoConfigStore, logger);
  const server = app.listen(config.PORT, "0.0.0.0", () => {
    logger.info({ port: config.PORT }, "HTTP server listening");
  });

  // Start outbox worker
  const outboxWorker = new OutboxWorker(pool, bitgoClient, config, logger);
  outboxWorker.start();

  // Start background jobs
  const pollPendingJob = new PollPendingJob(pool, bitgoClient, config, logger);
  pollPendingJob.start();

  const reconciliationJob = new ReconciliationJob(pool, config, logger);
  reconciliationJob.start();

  logger.info("Background jobs started");

  // Graceful shutdown
  const shutdown = async () => {
    logger.info("Shutting down withdrawals service");

    // Stop background jobs
    outboxWorker.stop();
    pollPendingJob.stop();
    reconciliationJob.stop();

    // Close HTTP server
    await new Promise<void>((resolve) => {
      server.close(() => {
        logger.info("HTTP server closed");
        resolve();
      });
    });

    // Close connections
    await pool.end();
    redis.disconnect();

    logger.info("Shutdown complete");
    process.exit(0);
  };

  process.on("SIGTERM", shutdown);
  process.on("SIGINT", shutdown);

  logger.info("Withdrawals service started successfully");
}

start().catch((error) => {
  logger.error({ error }, "Failed to start withdrawals service");
  process.exit(1);
});
