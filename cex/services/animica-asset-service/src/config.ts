/**
 * Animica Asset Service Configuration
 */

import { z } from "zod";
import { baseEnvSchema, loadEnv } from "@cex/common";

const configSchema = baseEnvSchema.partial().extend({
  SERVICE_NAME: z.string().default("animica-asset-service"),
  LOG_LEVEL: z.string().default("info"),
  
  // Optional common fields (not needed for this service)
  NATS_URL: z.string().url().optional(),
  REDIS_URL: z.string().url().optional(),
  
  // Animica RPC configuration
  ANIMICA_RPC_URL: z.string().default("http://127.0.0.1:8545/rpc"),
  ANIMICA_NETWORK: z.enum(["mainnet", "testnet"]).default("mainnet"),
  ANIMICA_ASSET_NETWORK_ID: z.string().default("ffffffff-0006-0006-0006-000000000006"),
  
  // Confirmation settings
  ANIMICA_CONFIRMATIONS_REQUIRED: z.coerce.number().default(20),
  ANIMICA_SCAN_START_HEIGHT: z.coerce.number().default(0),
  ANIMICA_SCAN_BATCH: z.coerce.number().default(200),
  ANIMICA_SCAN_POLL_MS: z.coerce.number().default(2000),
  ANIMICA_MAX_REORG_DEPTH: z.coerce.number().default(200),
  
  // Wallet configuration
  ANIMICA_WALLET_MODE: z.enum(["hotwallet", "watch"]).default("hotwallet"),
  ANIMICA_HOT_WALLET_LABEL: z.string().default("exchange_hot"),
  ANIMICA_HOT_WALLET_ADDRESS: z.string().optional(),
  
  // Fee configuration
  ANIMICA_FEE_POLICY: z.enum(["dynamic", "fixed"]).default("dynamic"),
  ANIMICA_FEE_RATE_ATOMS_PER_BYTE: z.coerce.number().optional(),
  ANIMICA_MIN_FEE_ATOMS: z.string().default("1000000000000000"), // 0.001 ANM
  ANIMICA_MAX_FEE_ATOMS: z.string().default("100000000000000000"), // 0.1 ANM
  
  // Ledger service integration
  LEDGER_SERVICE_URL: z.string().default("http://localhost:13004"),
  
  // Admin
  ADMIN_API_KEY: z.string(),
  
  // Background processing
  SCAN_WORKER_INTERVAL_MS: z.coerce.number().default(5000), // 5 seconds
  WITHDRAWAL_POLL_INTERVAL_MS: z.coerce.number().default(30000), // 30 seconds
  RECONCILE_INTERVAL_MS: z.coerce.number().default(300000), // 5 minutes
  
  // RPC client settings
  RPC_TIMEOUT_MS: z.coerce.number().default(30000),
  RPC_MAX_RETRIES: z.coerce.number().default(3),
  RPC_RETRY_DELAY_MS: z.coerce.number().default(1000),
  
  // Leader election
  SCAN_LOCK_TTL_MS: z.coerce.number().default(30000), // 30 seconds
  INSTANCE_ID: z.string().default(() => `animica-${Date.now()}-${Math.random().toString(36).slice(2)}`),
});

export type Config = z.infer<typeof configSchema>;

export function loadConfig(): Config {
  return loadEnv(configSchema);
}
