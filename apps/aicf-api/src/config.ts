import { z } from 'zod';

const envSchema = z.object({
  AICF_API_HOST: z.string().default('0.0.0.0'),
  AICF_API_PORT: z.coerce.number().int().positive().default(8099),
  AICF_API_JWT_SECRET: z.string().min(8).default('dev-secret-change-me'),
  AICF_API_INTERNAL_SECRET: z.string().min(8).default('dev-internal-secret'),
  AICF_CHAIN_ID: z.coerce.number().int().positive().default(1337),
  AICF_TREASURY_ADDRESS: z.string().default('anm1treasurydev'),
  AICF_GOVERNANCE_ADDRESS: z.string().default('anm1governancedev'),
  AICF_PROJECT_BALANCE_CONTRACT: z.string().default('anm1projectbalancedev'),
  AICF_JOB_ESCROW_CONTRACT: z.string().default('anm1jobescrowdev'),
  AICF_REWARDS_CONTRACT: z.string().default('anm1rewardsdev'),
  AICF_PROVIDER_REGISTRY_CONTRACT: z.string().default('anm1providersdev'),
  AICF_STAKE_MANAGER_CONTRACT: z.string().default('anm1stakemanagerdev'),
  AICF_DISPUTE_MANAGER_CONTRACT: z.string().default('anm1disputesdev'),
  AICF_MIN_PROVIDER_STAKE_ANM: z.coerce.number().positive().default(1500),
  AICF_CHALLENGE_WINDOW_SECONDS: z.coerce.number().int().positive().default(900),
  AICF_DEFAULT_SUBSIDY_BPS: z.coerce.number().int().min(0).max(10000).default(500),
  AICF_ADMIN_BOOTSTRAP_EMAIL: z.string().email().default('admin@animica.org'),
  AICF_ADMIN_BOOTSTRAP_PASSWORD: z.string().min(8).default('animica-admin-change-me'),
  AICF_TREASURY_BOOTSTRAP_ANM: z.coerce.number().positive().default(500000),
  AICF_RATE_LIMIT_WINDOW_MS: z.coerce.number().int().positive().default(60000),
  AICF_RATE_LIMIT_MAX: z.coerce.number().int().positive().default(240),
  AICF_MINER_ARTIFACTS_DIR: z.string().default('artifacts/miners'),
  AICF_PROVIDER_ARTIFACTS_DIR: z.string().default('dist/provider')
});

export type AppConfig = z.infer<typeof envSchema>;

export function loadConfig(env: NodeJS.ProcessEnv = process.env): AppConfig {
  return envSchema.parse(env);
}
