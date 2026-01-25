import express from "express";
import { v4 as uuidv4 } from "uuid";
import { z } from "zod";
import {
  baseEnvSchema,
  connectNats,
  createLogger,
  createPgPool,
  createRedis,
  jsonCodec,
  loadEnv,
  orderSubmitSchema,
  subjects
} from "@cex/common";

const env = loadEnv(
  baseEnvSchema.extend({
    SERVICE_NAME: z.string().default("matching-engine")
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

  const server = app.listen(env.PORT, () => {
    logger.info({ port: env.PORT }, "matching-engine listening");
  });

  const subscription = nats.subscribe(subjects.orderSubmit, {
    queue: "matching-engine"
  });

  (async () => {
    for await (const msg of subscription) {
      const decoded = jsonCodec.decode(msg.data);
      const parsed = orderSubmitSchema.safeParse(decoded);
      if (!parsed.success) {
        logger.warn({ errors: parsed.error.flatten() }, "invalid order submit payload");
        continue;
      }

      const order = parsed.data;
      const accepted = {
        event_id: uuidv4(),
        correlation_id: order.correlation_id ?? order.event_id,
        causation_id: order.event_id,
        created_at: new Date().toISOString(),
        idempotency_key: order.client_order_id,
        type: "OrderAccepted",
        order_id: uuidv4(),
        user_id: order.user_id,
        client_order_id: order.client_order_id,
        market: order.market,
        side: order.side,
        price: order.price,
        quantity: order.quantity
      };

      nats.publish(subjects.orderAccepted, jsonCodec.encode(accepted));
      logger.info({ orderId: accepted.order_id }, "order accepted");
    }
  })().catch((error) => {
    logger.error({ error }, "subscription error");
  });

  const shutdown = async () => {
    subscription.unsubscribe();
    await nats.drain();
    await pgPool.end();
    redis.disconnect();
    server.close();
  };

  process.on("SIGTERM", shutdown);
  process.on("SIGINT", shutdown);
};

start().catch((error) => {
  logger.error({ error }, "failed to start matching-engine");
  process.exit(1);
});
