/**
 * BitGo Webhook Ingestor Service
 * 
 * Main entry point - sets up HTTP server and background jobs
 */

import { createLogger as createBaseLogger, createPgPool, createRedis, connectNats } from "@cex/common";
import { createLogger } from "@cex/observability";
import { configureSecrets, getSecret } from "@cex/security/secrets";
import { loadConfig } from "./config.js";
import { createServer } from "./http/server.js";
import { OutboxProcessor, ConfirmationBackfill } from "./jobs/index.js";

const config = loadConfig();

// Initialize structured logger
const logger = createLogger({
  service: config.SERVICE_NAME,
  environment: config.NODE_ENV,
  level: config.LOG_LEVEL,
  prettyPrint: config.NODE_ENV === "development",
  redact: true, // Enable automatic redaction of sensitive data
});

async function start() {
  logger.info(
    {
      service: config.SERVICE_NAME,
      environment: config.NODE_ENV,
      port: config.PORT,
    },
    "Starting BitGo webhook ingestor service"
  );

  // Initialize secrets management
  // Default: uses environment variables via EnvSecretProvider
  // For production, consider upgrading to cloud secret managers:
  // 
  // AWS Secrets Manager:
  //   import { AwsSecretProvider } from "@cex/security/secrets";
  //   configureSecrets(new AwsSecretProvider({ region: "us-east-1", secretName: "bitgo-webhook-ingestor/prod" }));
  //
  // GCP Secret Manager:
  //   import { GcpSecretProvider } from "@cex/security/secrets";
  //   configureSecrets(new GcpSecretProvider({ projectId: "my-project", prefix: "bitgo-webhook-ingestor-prod" }));
  
  // Load sensitive configuration from secrets
  const bitgoWebhookSecret = await getSecret("BITGO_WEBHOOK_SECRET");
  const bitgoApiToken = await getSecret("BITGO_API_TOKEN");
  const bitgoAccessToken = await getSecret("BITGO_ACCESS_TOKEN");
  const adminKey = await getSecret("ADMIN_KEY");
  const serviceAuthKey = await getSecret("SERVICE_AUTH_KEY");

  // Update config with loaded secrets
  const runtimeConfig = {
    ...config,
    BITGO_WEBHOOK_SECRET: bitgoWebhookSecret || config.BITGO_WEBHOOK_SECRET,
    BITGO_API_TOKEN:
      bitgoApiToken ||
      config.BITGO_API_TOKEN ||
      bitgoAccessToken ||
      config.BITGO_ACCESS_TOKEN,
    BITGO_ACCESS_TOKEN:
      bitgoAccessToken ||
      config.BITGO_ACCESS_TOKEN ||
      bitgoApiToken ||
      config.BITGO_API_TOKEN,
    ADMIN_KEY: adminKey || config.ADMIN_KEY,
    SERVICE_AUTH_KEY: serviceAuthKey || config.SERVICE_AUTH_KEY,
  };

  logger.info(
    {
      bitgoSecretConfigured: !!runtimeConfig.BITGO_WEBHOOK_SECRET,
      bitgoApiConfigured: !!runtimeConfig.BITGO_API_TOKEN,
      adminKeyConfigured: !!runtimeConfig.ADMIN_KEY,
      serviceAuthConfigured: !!runtimeConfig.SERVICE_AUTH_KEY,
    },
    "Secrets loaded"
  );

  // Initialize connections
  const pool = createPgPool(runtimeConfig as any);
  const redis = createRedis(runtimeConfig as any);
  const nats = await connectNats(runtimeConfig as any);

  logger.info("Database and message bus connections established");

  // Create HTTP server
  const app = createServer(pool, redis, runtimeConfig, logger);
  const server = app.listen(runtimeConfig.PORT, "0.0.0.0", () => {
    logger.info({ port: runtimeConfig.PORT }, "HTTP server listening");
  });

  // Start background jobs
  const outboxProcessor = new OutboxProcessor(pool, nats, runtimeConfig, logger);
  outboxProcessor.start();

  const confirmationBackfill = new ConfirmationBackfill(pool, runtimeConfig, logger);
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
