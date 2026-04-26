/**
 * Ledger Service - Main Entry Point
 * 
 * Double-entry accounting service that consumes trade and order events
 * from the matching engine and maintains accurate user balances.
 */

import express from "express";
import { loadConfig } from "./config.js";
import { createLogger, connectNats } from "@cex/common";
import pg from "pg";
import { LedgerConsumer } from "./consumers/nats_consumer.js";
import { setupAdminAPI } from "./api/http.js";
import { runReconciliation, checkHealth } from "./jobs/index.js";
import type { Market } from "./domain/types.js";

const config = loadConfig();
const logger = createLogger(config.SERVICE_NAME, config.LOG_LEVEL);

async function start() {
  logger.info({ config: { ...config, ADMIN_KEY: config.ADMIN_KEY ? "***" : undefined } }, "Starting ledger service");

  // Initialize connections
  const pool = new pg.Pool({
    connectionString: String(config.DATABASE_URL),
    host: process.env.DB_HOST || "127.0.0.1",
    port: Number(process.env.DB_PORT || 5432),
    user: String(process.env.DB_USER || "cex"),
    password: String(process.env.DB_PASSWORD || "glassrock1212"),
    database: String(process.env.DB_NAME || "cex_exchange"),
  });
  
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
    const hasActive = await client.query(`
      SELECT EXISTS (
        SELECT 1
        FROM information_schema.columns
        WHERE table_name = 'markets' AND column_name = 'active'
      ) AS exists
    `);

    const hasBaseAsset = await client.query(`
      SELECT EXISTS (
        SELECT 1
        FROM information_schema.columns
        WHERE table_name = 'markets' AND column_name = 'base_asset'
      ) AS exists
    `);

    const hasQuoteAsset = await client.query(`
      SELECT EXISTS (
        SELECT 1
        FROM information_schema.columns
        WHERE table_name = 'markets' AND column_name = 'quote_asset'
      ) AS exists
    `);

    const hasMakerFeeBps = await client.query(`
      SELECT EXISTS (
        SELECT 1
        FROM information_schema.columns
        WHERE table_name = 'markets' AND column_name = 'maker_fee_bps'
      ) AS exists
    `);

    const hasTakerFeeBps = await client.query(`
      SELECT EXISTS (
        SELECT 1
        FROM information_schema.columns
        WHERE table_name = 'markets' AND column_name = 'taker_fee_bps'
      ) AS exists
    `);

    const hasFeeAsset = await client.query(`
      SELECT EXISTS (
        SELECT 1
        FROM information_schema.columns
        WHERE table_name = 'markets' AND column_name = 'fee_asset'
      ) AS exists
    `);

    const whereClause = hasActive.rows[0]?.exists ? "WHERE active = true" : "";
    const result = await client.query(`
      SELECT *
      FROM markets
      ${whereClause}
      ORDER BY symbol
    `);

    return result.rows.map((row: any) => ({
      id: row.id,
      symbol: row.symbol,
      baseAsset:
        hasBaseAsset.rows[0]?.exists ? row.base_asset :
        row.base || row.base_symbol || row.baseAsset || "",
      quoteAsset:
        hasQuoteAsset.rows[0]?.exists ? row.quote_asset :
        row.quote || row.quote_symbol || row.quoteAsset || "",
      makerFeeBps:
        hasMakerFeeBps.rows[0]?.exists ? Number(row.maker_fee_bps ?? 0) : 0,
      takerFeeBps:
        hasTakerFeeBps.rows[0]?.exists ? Number(row.taker_fee_bps ?? 0) : 0,
      feeAsset:
        hasFeeAsset.rows[0]?.exists
          ? row.fee_asset
          : (hasQuoteAsset.rows[0]?.exists ? row.quote_asset : row.quote || row.quote_symbol || "")
    }));
  } finally {
    client.release();
  }
}

// Start the service
start().catch((error) => {
  logger.error(
    {
      error,
      message: error instanceof Error ? error.message : String(error),
      stack: error instanceof Error ? error.stack : undefined,
    },
    "Failed to start ledger service"
  );
  process.exit(1);
});
