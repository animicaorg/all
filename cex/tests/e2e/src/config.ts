/**
 * E2E Test Configuration
 * 
 * Manages configuration for E2E test execution including:
 * - Test parameters (duration, rates, markets)
 * - Service endpoints
 * - Feature flags (BitGo mode, chaos testing)
 * - Report paths
 */

import { randomInt } from 'crypto';

export interface E2EConfig {
  // Test execution
  scenario: string;
  duration: number; // seconds
  rate: number; // orders per second
  markets: string[];
  seed: number; // for deterministic RNG
  
  // Feature flags
  useBitGoSandbox: boolean;
  enableChaos: boolean;
  keepStackRunning: boolean;
  
  // Endpoints
  apiGateway: string;
  adminAPI: string;
  websocketURL: string;
  animicaRPC: string;
  
  // Reporting
  reportJSON: string;
  reportMarkdown: string;
  artifactsDir: string;
  
  // Infrastructure
  dbHost: string;
  dbPort: number;
  dbName: string;
  dbUser: string;
  dbPassword: string;
  
  redisHost: string;
  redisPort: number;
  
  natsURL: string;
  
  // BitGo (optional)
  bitgoEnv?: 'sandbox' | 'mock';
  bitgoAccessToken?: string;
  bitgoWebhookSecret?: string;
  
  // Chaos (optional)
  toxiproxyHost?: string;
  toxiproxyPort?: number;
}

/**
 * Parse command-line arguments and environment variables
 */
export function loadConfig(): E2EConfig {
  const args = parseArgs(process.argv.slice(2));
  
  // Generate or use provided seed for reproducibility
  const seed = args.seed ? parseInt(args.seed) : randomInt(0, 1000000);
  
  const config: E2EConfig = {
    // Test execution
    scenario: args.scenario || 'smoke',
    duration: parseInt(args.duration || '120'), // 2 minutes default
    rate: parseInt(args.rate || '10'), // 10 orders/sec default
    markets: args.markets ? args.markets.split(',') : ['ANM-USD'],
    seed,
    
    // Feature flags
    useBitGoSandbox: args['use-bitgo-sandbox'] === 'true',
    enableChaos: args.chaos === 'true',
    keepStackRunning: args.keep === 'true',
    
    // Endpoints (from env or defaults)
    apiGateway: process.env.API_GATEWAY_URL || 'http://localhost:3000',
    adminAPI: process.env.ADMIN_API_URL || 'http://localhost:3001',
    websocketURL: process.env.WS_URL || 'ws://localhost:3000',
    animicaRPC: process.env.ANIMICA_RPC || 'http://localhost:8545',
    
    // Reporting
    reportJSON: args['report-json'] || `./artifacts/report-${Date.now()}.json`,
    reportMarkdown: args['report-md'] || `./artifacts/report-${Date.now()}.md`,
    artifactsDir: './artifacts',
    
    // Infrastructure
    dbHost: process.env.DB_HOST || 'localhost',
    dbPort: parseInt(process.env.DB_PORT || '5432'),
    dbName: process.env.DB_NAME || 'cex_e2e',
    dbUser: process.env.DB_USER || 'cex',
    dbPassword: process.env.DB_PASSWORD || 'secret',
    
    redisHost: process.env.REDIS_HOST || 'localhost',
    redisPort: parseInt(process.env.REDIS_PORT || '6379'),
    
    natsURL: process.env.NATS_URL || 'nats://localhost:4222',
    
    // BitGo (optional)
    bitgoEnv: (process.env.BITGO_ENV as 'sandbox' | 'mock') || 'mock',
    bitgoAccessToken: process.env.BITGO_ACCESS_TOKEN,
    bitgoWebhookSecret: process.env.BITGO_WEBHOOK_SECRET,
    
    // Chaos (optional)
    toxiproxyHost: process.env.TOXIPROXY_HOST || 'localhost',
    toxiproxyPort: parseInt(process.env.TOXIPROXY_PORT || '8474'),
  };
  
  return config;
}

/**
 * Simple argument parser
 */
function parseArgs(argv: string[]): Record<string, string> {
  const args: Record<string, string> = {};
  
  for (let i = 0; i < argv.length; i++) {
    const arg = argv[i];
    
    if (arg.startsWith('--')) {
      const key = arg.slice(2);
      const value = argv[i + 1];
      
      if (value && !value.startsWith('--')) {
        args[key] = value;
        i++;
      } else {
        args[key] = 'true';
      }
    }
  }
  
  return args;
}

/**
 * Validate configuration
 */
export function validateConfig(config: E2EConfig): void {
  const errors: string[] = [];
  
  // Validate scenario
  const validScenarios = [
    'smoke',
    'market_maker',
    'stress',
    'deposits_bitgo',
    'deposits_animica',
    'withdrawals_bitgo',
    'withdrawals_animica',
    'chaos_kill_restart',
    'chaos_partition',
    'reorg_animica',
    'reconciliation_proof',
    'all'
  ];
  
  if (!validScenarios.includes(config.scenario)) {
    errors.push(`Invalid scenario: ${config.scenario}. Must be one of: ${validScenarios.join(', ')}`);
  }
  
  // Validate numeric params
  if (config.duration <= 0) {
    errors.push(`Duration must be positive: ${config.duration}`);
  }
  
  if (config.rate <= 0) {
    errors.push(`Rate must be positive: ${config.rate}`);
  }
  
  // Validate markets
  if (config.markets.length === 0) {
    errors.push('At least one market must be specified');
  }
  
  // Validate BitGo sandbox requirements
  if (config.useBitGoSandbox) {
    if (!config.bitgoAccessToken) {
      errors.push('BitGo sandbox requires BITGO_ACCESS_TOKEN');
    }
    if (!config.bitgoWebhookSecret) {
      errors.push('BitGo sandbox requires BITGO_WEBHOOK_SECRET');
    }
  }
  
  if (errors.length > 0) {
    throw new Error(`Configuration validation failed:\n${errors.join('\n')}`);
  }
}

/**
 * Print configuration summary
 */
export function printConfig(config: E2EConfig): void {
  console.log('═══════════════════════════════════════════════════════════════');
  console.log('  E2E Test Configuration');
  console.log('═══════════════════════════════════════════════════════════════');
  console.log(`  Scenario:         ${config.scenario}`);
  console.log(`  Duration:         ${config.duration}s`);
  console.log(`  Rate:             ${config.rate} orders/sec`);
  console.log(`  Markets:          ${config.markets.join(', ')}`);
  console.log(`  Seed:             ${config.seed}`);
  console.log(`  BitGo Mode:       ${config.bitgoEnv}`);
  console.log(`  Chaos Enabled:    ${config.enableChaos}`);
  console.log(`  Keep Running:     ${config.keepStackRunning}`);
  console.log('═══════════════════════════════════════════════════════════════');
  console.log();
}
