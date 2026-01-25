/**
 * BitGo Webhook Ingestor Service
 * 
 * Main entry point - sets up HTTP server and background jobs
 */

import { createLogger, createPgPool, createRedis, connectNats } from "@cex/common";
import { loadConfig } from "./config.js";
import { createServer } from "./http/server.js";
import { OutboxProcessor, ConfirmationBackfill } from "./jobs/index.js";

const config = loadConfig();
const logger = createLogger(config.SERVICE_NAME, config.LOG_LEVEL);

async function start() {
  logger.info(
    {
      config: {
        ...config,
        BITGO_WEBHOOK_SECRET: config.BITGO_WEBHOOK_SECRET ? "***" : undefined,
        BITGO_API_TOKEN: config.BITGO_API_TOKEN ? "***" : undefined,
        ADMIN_KEY: config.ADMIN_KEY ? "***" : undefined,
      },
    },
    "Starting BitGo webhook ingestor service"
  );

  // Initialize connections
  const pool = createPgPool(config as any);
  const redis = createRedis(config as any);
  const nats = await connectNats(config as any);

  logger.info("Database and message bus connections established");

  // Create HTTP server
  const app = createServer(pool, redis, config, logger);
  const server = app.listen(config.PORT, () => {
    logger.info({ port: config.PORT }, "HTTP server listening");
  });

  // Start background jobs
  const outboxProcessor = new OutboxProcessor(pool, nats, config, logger);
  outboxProcessor.start();

  const confirmationBackfill = new ConfirmationBackfill(pool, config, logger);
  confirmationBackfill.start();

  logger.info("Background jobs started");

  // Graceful shutdown
  const shutdown = async () => {
    logger.info("Shutting down BitGo webhook ingestor service");

    // Stop background jobs
    outboxProcessor.stop();
    confirmationBackfill.stop();

    // Close HTTP server
    await new Promise<void>((resolve) => {
      server.close(() => {
        logger.info("HTTP server closed");
        resolve();
      });
    });

    // Close connections
    await nats.drain();
    await pool.end();
    redis.disconnect();

    logger.info("Shutdown complete");
    process.exit(0);
  };

  process.on("SIGTERM", shutdown);
  process.on("SIGINT", shutdown);

  logger.info("BitGo webhook ingestor service started successfully");
}

start().catch((error) => {
  logger.error({ error }, "Failed to start BitGo webhook ingestor service");
  process.exit(1);
});
