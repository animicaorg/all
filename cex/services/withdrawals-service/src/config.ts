/**
 * Withdrawals Service Configuration
 */

import { z } from "zod";
import { baseEnvSchema, loadEnv } from "@cex/common";

const configSchema = baseEnvSchema.extend({
  SERVICE_NAME: z.string().default("withdrawals-service"),
  
  // BitGo configuration
  BITGO_ENV: z.enum(["prod", "test"]).default("test"),
  BITGO_ACCESS_TOKEN: z.string(),
  BITGO_WEBHOOK_SECRET: z.string().optional(),
  BITGO_BASE_URL: z.string().optional(),
  BITGO_EXPRESS_URL: z.string().optional(),
  BITGO_WALLET_PASSPHRASE: z.string().optional(),
  CONFIG_ENCRYPTION_KEY: z.string().optional(),
  
  // Ledger service integration
  LEDGER_SERVICE_URL: z.string().default("http://localhost:13004"),
  
  // Admin
  ADMIN_API_KEY: z.string(),
  
  // Background processing
  OUTBOX_WORKER_INTERVAL_MS: z.coerce.number().default(5000), // 5 seconds
  POLL_PENDING_INTERVAL_MS: z.coerce.number().default(60000), // 1 minute
  
  // Rate limiting
  WITHDRAWAL_REQUEST_RATE_LIMIT: z.coerce.number().default(5),
  
  // JWT authentication (optional - can be extended later)
  JWT_SECRET: z.string().optional(),
});

export type Config = z.infer<typeof configSchema>;

export function loadConfig(): Config {
  const config = loadEnv(configSchema);
  
  // Set default BitGo base URL based on environment if not provided
  if (!config.BITGO_BASE_URL) {
    config.BITGO_BASE_URL = config.BITGO_ENV === "prod" 
      ? "https://app.bitgo.com" 
      : "https://app.bitgo-test.com";
  }
  
  return config;
}
