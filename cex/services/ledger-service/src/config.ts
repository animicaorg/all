/**
 * Configuration for ledger service
 */

import { z } from "zod";
import { loadEnv } from "@cex/common";

export const configSchema = z.object({
  SERVICE_NAME: z.string().default("ledger-service"),
  PORT: z.coerce.number().default(3003),
  LOG_LEVEL: z.string().default("info"),
  
  // Database
  DATABASE_URL: z.string(),
  
  // NATS
  NATS_URL: z.string().default("nats://localhost:4222"),
  
  // Redis
  REDIS_URL: z.string().default("redis://localhost:6379"),
  
  // Admin API
  ADMIN_KEY: z.string().optional(),
  
  // Reconciliation
  AUTO_FIX_BALANCES: z.string().default("false"),
  RECONCILE_INTERVAL_MS: z.coerce.number().default(86400000), // 24 hours
  
  // Health
  HEALTH_CHECK_INTERVAL_MS: z.coerce.number().default(60000), // 1 minute
});

export type Config = z.infer<typeof configSchema>;

export function loadConfig(): Config {
  return loadEnv(configSchema);
}
