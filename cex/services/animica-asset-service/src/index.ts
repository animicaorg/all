/**
 * Animica Asset Service
 * 
 * Main entry point - sets up HTTP server and background jobs
 */

import { createLogger, createPgPool } from "@cex/common";
import { loadConfig } from "./config.js";
import { createServer } from "./api/server.js";
import { createAnimicaRpcClient } from "./rpc/client.js";
import { ScanLoopJob, PollWithdrawalsJob, ReconciliationJob, AnimicaOutboxProcessor } from "./jobs/index.js";

const config = loadConfig();
const logger = createLogger(config.SERVICE_NAME, config.LOG_LEVEL);

async function start() {
  logger.info(
    {
      config: {
        ...config,
        ADMIN_API_KEY: config.ADMIN_API_KEY ? "***" : undefined,
      },
    },
    "Starting Animica asset service"
  );

  // Initialize database connection
  const pool = createPgPool(config as any);
  logger.info("Database connection established");

  // Create RPC client
  const rpcClient = createAnimicaRpcClient({
    url: config.ANIMICA_RPC_URL,
    timeout: config.RPC_TIMEOUT_MS,
    maxRetries: config.RPC_MAX_RETRIES,
    retryDelay: config.RPC_RETRY_DELAY_MS,
    logger,
  });

  // Detect RPC capabilities
  try {
    await rpcClient.detectCapabilities();
  } catch (error) {
    logger.warn({ error }, "Failed to detect RPC capabilities, continuing anyway");
  }

  // Check node health
  const healthy = await rpcClient.health();
  if (!healthy) {
    logger.error("Animica node is not healthy");
    process.exit(1);
  }

  logger.info("Animica RPC connection established");

  // Create HTTP server
  const app = createServer(pool, rpcClient, config, logger);
  const server = app.listen(Number(config.PORT ?? 3000), "0.0.0.0", () => {
    logger.info({ port: Number(config.PORT ?? 3000) }, "HTTP server listening");
  });

  // Start background jobs
  const scanLoopJob = new ScanLoopJob(pool, rpcClient, config, logger);
  scanLoopJob.start();

  const pollWithdrawalsJob = new PollWithdrawalsJob(pool, rpcClient, config, logger);
  pollWithdrawalsJob.start();

  const reconciliationJob = new ReconciliationJob(pool, rpcClient, config, logger);
  reconciliationJob.start();

  const outboxProcessor = new AnimicaOutboxProcessor(pool, config, logger);
  outboxProcessor.start();

  logger.info("Background jobs started");
  logger.info("Animica asset service started successfully");

  // Graceful shutdown
  const shutdown = async () => {
    logger.info("Shutting down Animica asset service");

    // Stop background jobs
    scanLoopJob.stop();
    pollWithdrawalsJob.stop();
    reconciliationJob.stop();
    outboxProcessor.stop();

    // Close HTTP server
    await new Promise<void>((resolve) => {
      server.close(() => {
        logger.info("HTTP server closed");
        resolve();
      });
    });

    // Close database connection
    await pool.end();
    logger.info("Database connection closed");

    logger.info("Shutdown complete");
    process.exit(0);
  };

  process.on("SIGTERM", shutdown);
  process.on("SIGINT", shutdown);

  // Handle uncaught errors
  process.on("uncaughtException", (error) => {
    logger.fatal({ error }, "Uncaught exception");
    shutdown();
  });

  process.on("unhandledRejection", (reason, promise) => {
    logger.fatal({ reason, promise }, "Unhandled promise rejection");
    shutdown();
  });
}

start().catch((error) => {
  logger.error({ error }, "Failed to start Animica asset service");
  process.exit(1);
});
