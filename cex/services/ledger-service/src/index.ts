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
  orderAcceptedSchema,
  subjects
} from "@cex/common";

const env = loadEnv(
  baseEnvSchema.extend({
    SERVICE_NAME: z.string().default("ledger-service")
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
    logger.info({ port: env.PORT }, "ledger-service listening");
  });

  const subscription = nats.subscribe(subjects.orderAccepted, {
    queue: "ledger-service"
  });

  (async () => {
    for await (const msg of subscription) {
      const decoded = jsonCodec.decode(msg.data);
      const parsed = orderAcceptedSchema.safeParse(decoded);
      if (!parsed.success) {
        logger.warn({ errors: parsed.error.flatten() }, "invalid order accepted payload");
        continue;
      }

      const event = parsed.data;
      const client = await pgPool.connect();
      try {
        await client.query("BEGIN");
        const processed = await client.query(
          "SELECT event_id FROM processed_events WHERE event_id = $1",
          [event.event_id]
        );
        if (processed.rowCount) {
          await client.query("ROLLBACK");
          logger.info({ eventId: event.event_id }, "event already processed");
          continue;
        }

        await client.query(
          "INSERT INTO journal_entries (id, event_id, account_id, asset, amount, direction, description) VALUES ($1, $2, $3, $4, $5, $6, $7)",
          [
            uuidv4(),
            event.event_id,
            event.user_id,
            event.side === "buy" ? "USDT" : "ANM",
            event.quantity,
            "debit",
            `Order accepted ${event.order_id}`
          ]
        );

        await client.query(
          "INSERT INTO processed_events (event_id, consumer) VALUES ($1, $2)",
          [event.event_id, env.SERVICE_NAME]
        );
        await client.query("COMMIT");

        nats.publish(
          subjects.ledgerEntryPosted,
          jsonCodec.encode({
            event_id: uuidv4(),
            correlation_id: event.correlation_id ?? event.event_id,
            causation_id: event.event_id,
            created_at: new Date().toISOString(),
            type: "LedgerEntryPosted",
            journal_event_id: event.event_id
          })
        );
      } catch (error) {
        await client.query("ROLLBACK");
        logger.error({ error }, "failed to post journal entry");
      } finally {
        client.release();
      }
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
  logger.error({ error }, "failed to start ledger-service");
  process.exit(1);
});
