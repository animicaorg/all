import { z } from 'zod';

const EnvSchema = z.object({
  NODE_ENV: z.enum(['development', 'test', 'production']).default('development'),
  USDAN_API_HOST: z.string().default('0.0.0.0'),
  USDAN_API_PORT: z.coerce.number().int().positive().default(8098),
  USDAN_API_LOG_LEVEL: z.string().default('info'),
  USDAN_API_JWT_SECRET: z.string().min(16),
  USDAN_API_ADMIN_API_KEY: z.string().min(8),
  USDAN_API_WEBHOOK_SECRET: z.string().min(8),
  USDAN_API_RATE_LIMIT_WINDOW_MS: z.coerce.number().int().positive().default(60_000),
  USDAN_API_RATE_LIMIT_MAX: z.coerce.number().int().positive().default(120),

  DATABASE_URL: z.string().url().optional(),

  ANIMICA_RPC_URL: z.string().default('http://127.0.0.1:8545/rpc'),
  ANIMICA_CHAIN_ID: z.coerce.number().int().positive().default(1337),
  ANIMICA_USDAN_TOKEN_ADDRESS: z.string().min(3),
  ANIMICA_USDAN_MINT_CONTROLLER_ADDRESS: z.string().min(3),
  ANIMICA_USDAN_REDEMPTION_CONTROLLER_ADDRESS: z.string().min(3),
  ANIMICA_MINT_SIGNER_PRIVATE_KEY: z.string().min(8),
  ANIMICA_MIN_CONFIRMATIONS: z.coerce.number().int().positive().default(6),

  MODERN_TREASURY_BASE_URL: z.string().url(),
  MODERN_TREASURY_API_KEY: z.string().min(6),
  MODERN_TREASURY_ORG_ID: z.string().min(3),
  MODERN_TREASURY_LEDGER_ID: z.string().min(3),
  MODERN_TREASURY_WEBHOOK_SECRET: z.string().min(8),
  MODERN_TREASURY_SOURCE_ACCOUNT_ID: z.string().min(3),
  MODERN_TREASURY_PAYOUT_ACCOUNT_ID: z.string().min(3),

  USDAN_RESERVE_ATTESTATION_CONTRACT: z.string().min(3),
  USDAN_RESERVE_MIN_COVERAGE_BPS: z.coerce.number().int().positive().default(10_000),
  USDAN_KYC_PROVIDER: z.string().default('internal'),
  USDAN_NOTIFICATIONS_FROM_EMAIL: z.string().email().default('noreply@usdan.animica'),

  USDAN_DATA_MODE: z.enum(['memory', 'prisma']).default('memory')
});

export type Config = z.infer<typeof EnvSchema>;

export function loadConfig(env: NodeJS.ProcessEnv = process.env): Config {
  return EnvSchema.parse(env);
}
