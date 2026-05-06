/**
 * BitGo Webhook Ingestor Configuration
 */

import { z } from "zod";
import { baseEnvSchema, loadEnv } from "@cex/common";

const configSchema = baseEnvSchema.extend({
  SERVICE_NAME: z.string().default("bitgo-webhook-ingestor"),
  
  // Environment
  NODE_ENV: z.enum(["development", "staging", "production"]).default("development"),
  
  // BitGo configuration (will be loaded from secrets)
  BITGO_WEBHOOK_SECRET: z.string().optional(),
  BITGO_API_TOKEN: z.string().optional(),
  BITGO_ACCESS_TOKEN: z.string().optional(),
  BITGO_ENV: z.enum(["prod", "test"]).default("test"),
  BITGO_BASE_URL: z.string().url().optional(),
  BITGO_API_URL: z.string().url().optional(),
  
  // Rate limiting
  WEBHOOK_RATE_LIMIT_PER_MINUTE: z.coerce.number().default(100),
  WEBHOOK_REPLAY_WINDOW_SECONDS: z.coerce.number().default(300), // 5 minutes
  
  // Confirmation tracking
  CONFIRMATION_BACKFILL_INTERVAL_MS: z.coerce.number().default(60000), // 1 minute
  BITGO_TRANSFER_DISCOVERY_LIMIT: z.coerce.number().int().positive().max(500).default(100),
  OUTBOX_PROCESSOR_INTERVAL_MS: z.coerce.number().default(5000), // 5 seconds
  
  // Admin and service auth
  ADMIN_KEY: z.string().optional(),
  SERVICE_AUTH_KEY: z.string().optional(), // For internal service-to-service calls
  
  // Ledger service integration
  LEDGER_SERVICE_URL: z.string().default("http://localhost:13004"),
  LEDGER_SERVICE_NATS_SUBJECT: z.string().default("ledger.deposit.credit"),
});

export type Config = z.infer<typeof configSchema>;

export function loadConfig(): Config {
  return loadEnv(configSchema);
}
