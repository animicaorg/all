/**
 * Configuration Module
 * Loads and validates environment variables
 */

import { z } from 'zod';

const configSchema = z.object({
  NODE_ENV: z.enum(['development', 'production', 'test']).default('development'),
  SERVICE_NAME: z.string().default('admin-api'),
  LOG_LEVEL: z.enum(['fatal', 'error', 'warn', 'info', 'debug', 'trace']).default('info'),
  
  // HTTP Server
  HTTP_PORT: z.coerce.number().default(4000),
  HTTP_HOST: z.string().default('0.0.0.0'),
  
  // Database
  DATABASE_URL: z.string(),
  
  // Redis
  REDIS_URL: z.string().optional(),
  
  // Authentication
  JWT_SECRET: z.string().min(32),
  JWT_EXPIRES_IN: z.string().default('1h'),
  REFRESH_TOKEN_EXPIRES_IN: z.string().default('7d'),
  SESSION_SECRET: z.string().min(32),
  ADMIN_BOOTSTRAP_SECRET: z.string().min(16),
  CONFIG_ENCRYPTION_KEY: z.string().min(32),
  
  // TOTP
  TOTP_ISSUER: z.string().default('Animica Admin'),
  TOTP_WINDOW: z.coerce.number().default(2),
  
  // CSRF
  CSRF_SECRET: z.string().min(32),
  
  // Rate Limiting
  RATE_LIMIT_LOGIN_MAX: z.coerce.number().default(5),
  RATE_LIMIT_LOGIN_WINDOW_MS: z.coerce.number().default(300000),
  RATE_LIMIT_ADMIN_PER_SESSION: z.coerce.number().default(60),
  RATE_LIMIT_ADMIN_WINDOW_MS: z.coerce.number().default(60000),
  
  // CORS
  ADMIN_WEB_URL: z.string().default('http://localhost:5173'),
  CORS_CREDENTIALS: z.coerce.boolean().default(true),
  
  // External Services
  EXCHANGE_API_URL: z.string().default('http://localhost:3000'),
  MATCHING_ENGINE_URL: z.string().default('http://localhost:3100'),
  LEDGER_SERVICE_URL: z.string().default('http://localhost:3200'),
  BITGO_ENV: z.enum(['test', 'prod']).default('test'),
  BITGO_API_URL: z.string().optional(),
  BITGO_ACCESS_TOKEN: z.string().optional(),
  ANIMICA_NODE_URL: z.string().default('http://localhost:8545'),
  
  // Withdrawal Approval Policy
  WITHDRAWAL_APPROVAL_TIER_1_LIMIT: z.coerce.number().default(10000),
  WITHDRAWAL_APPROVAL_TIER_1_REQUIRED: z.coerce.number().default(2),
  WITHDRAWAL_APPROVAL_TIER_2_LIMIT: z.coerce.number().default(100000),
  WITHDRAWAL_APPROVAL_TIER_2_REQUIRED: z.coerce.number().default(3),
  WITHDRAWAL_APPROVAL_TIER_3_REQUIRED: z.coerce.number().default(3),
  WITHDRAWAL_APPROVAL_REQUIRE_SUPERADMIN_ABOVE: z.coerce.number().default(100000),
});

export type Config = z.infer<typeof configSchema>;

export function loadConfig(): Config {
  try {
    return configSchema.parse(process.env);
  } catch (error) {
    if (error instanceof z.ZodError) {
      const missing = error.issues
        .filter((issue) => issue.code === 'invalid_type')
        .map((issue) => issue.path.join('.'));
      throw new Error(`Missing or invalid environment variables: ${missing.join(', ')}`);
    }
    throw error;
  }
}
