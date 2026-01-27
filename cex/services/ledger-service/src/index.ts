/**
 * Ledger Service - Main Entry Point
 * 
 * Double-entry accounting service that consumes trade and order events
 * from the matching engine and maintains accurate user balances.
 */

import express from "express";
import { loadConfig } from "./config.js";
import { createLogger, createPgPool, connectNats } from "@cex/common";
import { LedgerConsumer } from "./consumers/nats_consumer.js";
import { setupAdminAPI } from "./api/http.js";
import { runReconciliation, checkHealth } from "./jobs/index.js";
import type { Market } from "./domain/types.js";

const config = loadConfig();
const logger = createLogger(config.SERVICE_NAME, config.LOG_LEVEL);

async function start() {
  logger.info({ config: { ...config, ADMIN_KEY: config.ADMIN_KEY ? "***" : undefined } }, "Starting ledger service");

  // Initialize connections
  const pool = createPgPool({
    DATABASE_URL: config.DATABASE_URL
  } as any);
  
  const nats = await connectNats({
    NATS_URL: config.NATS_URL
  } as any);

  // Setup HTTP server
  const app = express();
  app.use(express.json());

  // Setup admin API
  setupAdminAPI(app, pool, logger, config.ADMIN_KEY);

  const server = app.listen(config.PORT, "0.0.0.0", () => {
    logger.info({ port: config.PORT }, "HTTP server listening");
  });

  // Load markets from database
  const markets = await loadMarkets(pool);
  logger.info({ count: markets.length }, "Loaded markets");

  // Start consumer for each market
  const consumer = new LedgerConsumer(pool, nats, logger);
  for (const market of markets) {
    await consumer.startMarket(market);
  }

  // Start deposit credit consumer
  await consumer.startDepositCredits();

  // Start periodic reconciliation job
  const reconcileInterval = setInterval(async () => {
    try {
      logger.info("Running scheduled reconciliation");
      const report = await runReconciliation(pool, logger);
      if (!report.ok) {
        logger.warn({ mismatchCount: report.mismatches.length }, "Reconciliation found mismatches");
      } else {
        logger.info("Reconciliation completed successfully");
      }
    } catch (error) {
      logger.error({ error }, "Reconciliation job failed");
    }
  }, config.RECONCILE_INTERVAL_MS);

  // Start periodic health check
  const healthInterval = setInterval(async () => {
    try {
      const health = await checkHealth(pool, logger);
      if (!health.ok) {
        logger.warn({ health }, "Health check failed");
      }
    } catch (error) {
      logger.error({ error }, "Health check failed");
    }
  }, config.HEALTH_CHECK_INTERVAL_MS);

  // Graceful shutdown
  const shutdown = async () => {
    logger.info("Shutting down ledger service");
    
    clearInterval(reconcileInterval);
    clearInterval(healthInterval);
    
    await consumer.stop();
    await nats.drain();
    await pool.end();
    
    server.close(() => {
      logger.info("HTTP server closed");
      process.exit(0);
    });
  };

  process.on("SIGTERM", shutdown);
  process.on("SIGINT", shutdown);

  logger.info("Ledger service started successfully");
}

/**
 * Load markets from database
 */
async function loadMarkets(pool: any): Promise<Market[]> {
  const client = await pool.connect();
  try {
    const result = await client.query(
      `SELECT id, symbol, base_asset, quote_asset, maker_fee_bps, taker_fee_bps, fee_asset
       FROM markets
       WHERE active = true
       ORDER BY symbol`
    );

    return result.rows.map((row: any) => ({
      id: row.id,
      symbol: row.symbol,
      baseAsset: row.base_asset,
      quoteAsset: row.quote_asset,
      makerFeeBps: row.maker_fee_bps,
      takerFeeBps: row.taker_fee_bps,
      feeAsset: row.fee_asset
    }));
  } finally {
    client.release();
  }
}

// Start the service
start().catch((error) => {
  logger.error({ error }, "Failed to start ledger service");
  process.exit(1);
});
