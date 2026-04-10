/**
 * Service Configuration
 */

import dotenv from 'dotenv';
import { z } from 'zod';

dotenv.config();

const configSchema = z.object({
  // Service
  NODE_ENV: z.enum(['development', 'production', 'test']).default('development'),
  SERVICE_NAME: z.string().default('exchange-api'),
  LOG_LEVEL: z.enum(['trace', 'debug', 'info', 'warn', 'error', 'fatal']).default('info'),

  // HTTP Server
  HTTP_PORT: z.coerce.number().default(3000),
  HTTP_HOST: z.string().default('0.0.0.0'),

  // WebSocket Server
  WS_PORT: z.coerce.number().default(3001),
  WS_HOST: z.string().default('0.0.0.0'),
  WS_HEARTBEAT_INTERVAL_MS: z.coerce.number().default(15000),
  WS_HEARTBEAT_TIMEOUT_MS: z.coerce.number().default(45000),
  WS_MAX_SUBSCRIPTIONS_PER_CLIENT: z.coerce.number().default(50),
  WS_MAX_OUTGOING_QUEUE_SIZE: z.coerce.number().default(1000),

  // Database
  DATABASE_URL: z.string(),

  // Redis (for rate limiting and caching)
  REDIS_URL: z.string().optional(),
  REDIS_HOST: z.string().default('localhost'),
  REDIS_PORT: z.coerce.number().default(6379),
  REDIS_PASSWORD: z.string().optional(),
  REDIS_DB: z.coerce.number().default(0),

  // Authentication
  API_KEY_TIMESTAMP_WINDOW_MS: z.coerce.number().default(30000), // ±30s
  API_KEY_NONCE_TTL_MS: z.coerce.number().default(300000), // 5 min
  JWT_SECRET: z.string().optional(),
  JWT_EXPIRES_IN: z.string().default('24h'),

  // Rate Limiting
  RATE_LIMIT_PUBLIC_PER_IP: z.coerce.number().default(120), // per minute
  RATE_LIMIT_PUBLIC_WINDOW_MS: z.coerce.number().default(60000),
  RATE_LIMIT_PRIVATE_PER_KEY: z.coerce.number().default(60), // per minute
  RATE_LIMIT_PRIVATE_WINDOW_MS: z.coerce.number().default(60000),
  RATE_LIMIT_PRIVATE_BURST: z.coerce.number().default(20),
  RATE_LIMIT_USER_AGGREGATE: z.coerce.number().default(240), // per minute

  // CORS
  CORS_ORIGIN: z.string().default('*'),
  CORS_CREDENTIALS: z.coerce.boolean().default(false),

  // Cache TTLs
  CACHE_ORDERBOOK_TTL_MS: z.coerce.number().default(250),
  CACHE_TICKER_TTL_MS: z.coerce.number().default(1000),
  CACHE_MARKETS_TTL_MS: z.coerce.number().default(60000),

  // Pagination
  MAX_PAGE_SIZE: z.coerce.number().default(100),
  DEFAULT_PAGE_SIZE: z.coerce.number().default(50),

  // Market Data
  ORDERBOOK_MAX_DEPTH: z.coerce.number().default(50),
  TRADES_MAX_LIMIT: z.coerce.number().default(100),

  // Internal Service URLs (if using HTTP instead of NATS)
  MATCHING_ENGINE_URL: z.string().optional(),
  LEDGER_SERVICE_URL: z.string().optional(),
  DEPOSITS_SERVICE_URL: z.string().optional(),
  WITHDRAWALS_SERVICE_URL: z.string().optional(),

  // NATS (if using message bus)
  NATS_URL: z.string().optional(),
  NATS_USER: z.string().optional(),
  NATS_PASS: z.string().optional(),
});

export type Config = z.infer<typeof configSchema>;

export function loadConfig(): Config {
  try {
    return configSchema.parse(process.env);
  } catch (error) {
    if (error instanceof z.ZodError) {
      console.error('Configuration validation failed:');
      console.error(error.errors);
      throw new Error('Invalid configuration');
    }
    throw error;
  }
}
