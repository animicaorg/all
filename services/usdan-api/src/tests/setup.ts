import type { Config } from '../config.js';
import { createLogger } from '../logger.js';
import { buildRuntime } from '../runtime.js';

export function createTestConfig(): Config {
  return {
    NODE_ENV: 'test',
    USDAN_API_HOST: '127.0.0.1',
    USDAN_API_PORT: 0,
    USDAN_API_LOG_LEVEL: 'silent',
    USDAN_API_JWT_SECRET: 'test-secret-test-secret',
    USDAN_API_ADMIN_API_KEY: 'admin-test-key',
    USDAN_API_WEBHOOK_SECRET: 'webhook-test-secret',
    USDAN_API_RATE_LIMIT_WINDOW_MS: 60_000,
    USDAN_API_RATE_LIMIT_MAX: 1000,
    DATABASE_URL: undefined,
    ANIMICA_RPC_URL: 'http://localhost:8545/rpc',
    ANIMICA_CHAIN_ID: 1337,
    ANIMICA_USDAN_TOKEN_ADDRESS: 'anim1token',
    ANIMICA_USDAN_MINT_CONTROLLER_ADDRESS: 'anim1mint',
    ANIMICA_USDAN_REDEMPTION_CONTROLLER_ADDRESS: 'anim1redeem',
    ANIMICA_MINT_SIGNER_PRIVATE_KEY: 'mint-signer-secret',
    ANIMICA_MIN_CONFIRMATIONS: 6,
    MODERN_TREASURY_BASE_URL: 'https://app.moderntreasury.com',
    MODERN_TREASURY_API_KEY: 'mt-key',
    MODERN_TREASURY_ORG_ID: 'org-1',
    MODERN_TREASURY_LEDGER_ID: 'ledger-1',
    MODERN_TREASURY_WEBHOOK_SECRET: 'mt-webhook-secret',
    MODERN_TREASURY_SOURCE_ACCOUNT_ID: 'source-1',
    MODERN_TREASURY_PAYOUT_ACCOUNT_ID: 'payout-1',
    USDAN_RESERVE_ATTESTATION_CONTRACT: 'anim1reserve',
    USDAN_RESERVE_MIN_COVERAGE_BPS: 10_000,
    USDAN_KYC_PROVIDER: 'internal',
    USDAN_NOTIFICATIONS_FROM_EMAIL: 'noreply@usdan.local',
    USDAN_DATA_MODE: 'memory'
  };
}

export function createTestRuntime() {
  const config = createTestConfig();
  const logger = createLogger(config);
  const runtime = buildRuntime(config, logger);

  return {
    config,
    runtime
  };
}
