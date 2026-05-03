/**
 * Matching Engine Service
 * 
 * Entry point for the matching engine service.
 * Currently configured to process commands for a single market.
 * For production, extend to handle multiple markets with worker pools.
 */

import express from "express";
import { createLogger, createPgPool, connectNats, createRedis, jsonCodec, subjects } from "@cex/common";
import { loadEnv } from "./config.js";
import { MarketWorker } from "./workers/market_worker.js";
import { OutboxPublisher } from "./outbox/publisher.js";
import { decimalToAtoms } from "./engine/deterministic.js";
import type { OrderSide, TimeInForce } from "./engine/types.js";

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

  const workersByMarketId = new Map<string, MarketWorker>();
  const marketIdsBySymbol = new Map<string, string>();

  const ensureWorker = async (marketId: string, symbol: string): Promise<MarketWorker> => {
    const existing = workersByMarketId.get(marketId);
    if (existing) return existing;

    const worker = new MarketWorker(marketId, pgPool, logger);
    await worker.initialize();
    workersByMarketId.set(marketId, worker);
    marketIdsBySymbol.set(symbol, marketId);
    return worker;
  };

  const loadActiveMarkets = async () => {
    const result = await pgPool.query("SELECT id, symbol FROM markets WHERE active = true ORDER BY symbol");
    for (const row of result.rows) {
      try {
        await ensureWorker(row.id, row.symbol);
      } catch (error) {
        logger.error({ error, marketId: row.id, symbol: row.symbol }, "Failed to initialize market worker");
      }
    }
    logger.info({ count: workersByMarketId.size }, "Matching engine active markets loaded");
  };

  const getWorkerBySymbol = async (symbol: string): Promise<{ marketId: string; worker: MarketWorker } | null> => {
    const cachedMarketId = marketIdsBySymbol.get(symbol);
    if (cachedMarketId) {
      const cachedWorker = workersByMarketId.get(cachedMarketId);
      if (cachedWorker) return { marketId: cachedMarketId, worker: cachedWorker };
    }

    const result = await pgPool.query("SELECT id, symbol FROM markets WHERE symbol = $1 AND active = true", [symbol]);
    const market = result.rows[0];
    if (!market) return null;

    const worker = await ensureWorker(market.id, market.symbol);
    return { marketId: market.id, worker };
  };

  const toSide = (value: string): OrderSide => value.toLowerCase() === "buy" ? "BUY" : "SELL";
  const toTimeInForce = (value: string | undefined): TimeInForce => {
    if (value === "IOC" || value === "FOK" || value === "POST_ONLY") return value;
    return "GTC";
  };

  await loadActiveMarkets();
  const marketRefreshInterval = setInterval(() => {
    loadActiveMarkets().catch((error) => logger.error({ error }, "Active market refresh failed"));
  }, 30_000);

  const orderSub = nats.subscribe(subjects.orderSubmit);
  (async () => {
    for await (const message of orderSub) {
      const command = jsonCodec.decode(message.data) as any;
      try {
        const target = await getWorkerBySymbol(command.market);
        if (!target) {
          logger.warn({ market: command.market }, "Rejecting order for inactive or missing market");
          continue;
        }

        const orderType = String(command.order_type ?? "LIMIT").toUpperCase();
        if (orderType === "MARKET") {
          await target.worker.placeMarketOrder({
            userId: command.user_id,
            clientOrderId: command.client_order_id,
            marketId: target.marketId,
            side: toSide(command.side),
            sizeAtoms: decimalToAtoms(String(command.quantity), 8),
            idempotencyKey: command.idempotency_key ?? command.event_id,
          });
          continue;
        }

        await target.worker.placeLimitOrder({
          userId: command.user_id,
          clientOrderId: command.client_order_id,
          marketId: target.marketId,
          side: toSide(command.side),
          priceAtoms: decimalToAtoms(String(command.price), 8),
          sizeAtoms: decimalToAtoms(String(command.quantity), 8),
          timeInForce: toTimeInForce(orderType),
          postOnly: orderType === "POST_ONLY",
          idempotencyKey: command.idempotency_key ?? command.event_id,
        });
      } catch (error) {
        logger.error({ error, command }, "Failed to process order submit command");
      }
    }
  })().catch((error) => logger.error({ error }, "Order submit subscription failed"));

  const cancelSub = nats.subscribe(subjects.orderCancel);
  (async () => {
    for await (const message of cancelSub) {
      const command = jsonCodec.decode(message.data) as any;
      try {
        const target = await getWorkerBySymbol(command.market);
        if (!target) {
          logger.warn({ market: command.market }, "Rejecting cancel for inactive or missing market");
          continue;
        }

        await target.worker.cancelOrder({
          userId: command.user_id,
          orderId: command.order_id,
          idempotencyKey: command.idempotency_key ?? command.event_id,
        });
      } catch (error) {
        logger.error({ error, command }, "Failed to process order cancel command");
      }
    }
  })().catch((error) => logger.error({ error }, "Order cancel subscription failed"));

  const shutdown = async () => {
    logger.info("Shutting down matching engine");
    clearInterval(marketRefreshInterval);
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
