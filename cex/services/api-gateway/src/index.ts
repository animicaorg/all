import express from "express";
import { v4 as uuidv4 } from "uuid";
import { z } from "zod";
import { OpenAPIRegistry, OpenApiGeneratorV3 } from "@asteasolutions/zod-to-openapi";
import {
  baseEnvSchema,
  connectNats,
  createLogger,
  createPgPool,
  createRedis,
  jsonCodec,
  loadEnv,
  subjects
} from "@cex/common";

const env = loadEnv(
  baseEnvSchema.extend({
    SERVICE_NAME: z.string().default("api-gateway")
  })
);

const logger = createLogger(env.SERVICE_NAME, env.LOG_LEVEL);

const start = async () => {
  const app = express();
  app.use(express.json());

  const registry = new OpenAPIRegistry();
  const healthResponseSchema = z.object({
    status: z.string(),
    service: z.string(),
    postgres: z.boolean(),
    redis: z.boolean(),
    nats: z.string()
  });

  registry.registerPath({
    method: "get",
    path: "/healthz",
    responses: {
      200: {
        description: "Health check response",
        content: {
          "application/json": {
            schema: healthResponseSchema
          }
        }
      }
    }
  });

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

  app.get("/openapi.json", (_req, res) => {
    const generator = new OpenApiGeneratorV3(registry.definitions);
    res.json(
      generator.generateDocument({
        openapi: "3.0.0",
        info: {
          title: "Animica CEX API Gateway",
          version: "0.1.0"
        }
      })
    );
  });

  const server = app.listen(env.PORT, "0.0.0.0", () => {
    logger.info({ port: env.PORT }, "api-gateway listening");
  });

  const orderCommand = {
    event_id: uuidv4(),
    correlation_id: uuidv4(),
    causation_id: uuidv4(),
    created_at: new Date().toISOString(),
    idempotency_key: "client-order-1",
    type: "OrderSubmit",
    user_id: uuidv4(),
    client_order_id: "client-order-1",
    market: "ANM/USDT",
    side: "buy",
    price: 1.25,
    quantity: 10
  };

  nats.publish(subjects.orderSubmit, jsonCodec.encode(orderCommand));
  logger.info({ subject: subjects.orderSubmit }, "published sample OrderSubmit");

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
  logger.error({ error }, "failed to start api-gateway");
  process.exit(1);
});
