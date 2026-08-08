// Animica Python Cloud — THE single place economic and operational constants live.
//
// Nothing in this subsystem may hardcode a price, a split, a quota or a limit. Everything is
// read from here, and everything here is env-overridable so an operator can retune without a
// deploy. Values that must survive a restart AND be editable by an admin at runtime live in
// the PricingPolicy table instead — this file supplies the bootstrap defaults for it.
//
// Money: integer nANM (1 ANM = 1e9 nANM). USD: integer cents. Never floats, ever.

import { NANM_PER_ANM } from '../config';

function env(name: string, fallback = ''): string {
  return process.env[name] ?? fallback;
}
function envInt(name: string, fallback: number): number {
  const v = process.env[name];
  if (v == null || v === '') return fallback;
  const n = Number(v);
  return Number.isFinite(n) ? n : fallback;
}
function envBig(name: string, fallback: bigint): bigint {
  const v = process.env[name];
  if (v == null || v === '') return fallback;
  try {
    return BigInt(v);
  } catch {
    return fallback;
  }
}
function envBool(name: string, fallback: boolean): boolean {
  const v = process.env[name];
  if (v == null || v === '') return fallback;
  return v === '1' || v.toLowerCase() === 'true';
}

// ---------------------------------------------------------------------------
// Feature flags (§57). Finished functionality ships enabled; these exist for staged rollout
// and for an operator kill switch, not to hide half-built features.
// ---------------------------------------------------------------------------

export const flags = {
  pythonCloud: envBool('PYTHON_CLOUD_ENABLED', true),
  marketplace: envBool('CLOUD_MARKETPLACE_ENABLED', true),
  agents: envBool('CLOUD_AGENTS_ENABLED', true),
  computeMarket: envBool('CLOUD_COMPUTE_MARKET_ENABLED', true),
  schedules: envBool('CLOUD_SCHEDULES_ENABLED', true),
  // On-chain deployment anchoring. Off => deployments still complete, but are marked
  // unanchored rather than pretending a txid exists.
  anchoring: envBool('CLOUD_ANCHOR_ENABLED', true),
  // Commercial (USD) subscriptions.
  billing: envBool('CLOUD_BILLING_ENABLED', true),
};

// ---------------------------------------------------------------------------
// Execution envelope. These are HARD server-side ceilings — a developer may configure less,
// never more. The sandbox enforces them again at the OS level.
// ---------------------------------------------------------------------------

export const limits = {
  maxSourceBytes: envInt('CLOUD_MAX_SOURCE_BYTES', 256 * 1024),
  maxTimeoutMs: envInt('CLOUD_MAX_TIMEOUT_MS', 300_000),
  defaultTimeoutMs: envInt('CLOUD_DEFAULT_TIMEOUT_MS', 30_000),
  minTimeoutMs: 1_000,
  maxMemoryMb: envInt('CLOUD_MAX_MEMORY_MB', 1024),
  defaultMemoryMb: envInt('CLOUD_DEFAULT_MEMORY_MB', 256),
  minMemoryMb: 64,
  maxPids: envInt('CLOUD_MAX_PIDS', 128),
  maxOutputBytes: envInt('CLOUD_MAX_OUTPUT_BYTES', 1024 * 1024),
  maxRequestBytes: envInt('CLOUD_MAX_REQUEST_BYTES', 512 * 1024),
  maxLogLines: envInt('CLOUD_MAX_LOG_LINES', 500),
  maxLogLineChars: envInt('CLOUD_MAX_LOG_LINE_CHARS', 2000),
  maxTmpfsMb: envInt('CLOUD_MAX_TMPFS_MB', 64),

  // Nested agent-to-agent calls (§18).
  maxCallDepth: envInt('CLOUD_MAX_CALL_DEPTH', 4),
  maxCallsPerExecution: envInt('CLOUD_MAX_CALLS_PER_EXECUTION', 16),
  maxAiCallsPerExecution: envInt('CLOUD_MAX_AI_CALLS_PER_EXECUTION', 8),
  maxAiTokensPerExecution: envInt('CLOUD_MAX_AI_TOKENS_PER_EXECUTION', 8192),

  // Ceilings on EVERY privileged host call, not just the obviously expensive ones. The mainnet
  // node shares this box and has a history of RPC/CPU starvation, so an execution must never be
  // able to turn itself into an unbounded RPC or outbound-HTTP amplifier.
  maxHostCallsPerExecution: envInt('CLOUD_MAX_HOST_CALLS_PER_EXECUTION', 200),
  maxChainCallsPerExecution: envInt('CLOUD_MAX_CHAIN_CALLS_PER_EXECUTION', 20),
  maxHttpCallsPerExecution: envInt('CLOUD_MAX_HTTP_CALLS_PER_EXECUTION', 10),
  maxStateCallsPerExecution: envInt('CLOUD_MAX_STATE_CALLS_PER_EXECUTION', 100),

  // Anonymous free-tier callers get a tighter execution envelope than authenticated ones: they
  // cannot be billed, so their worst case must be small (§35, §78).
  anonMaxConcurrent: envInt('CLOUD_ANON_MAX_CONCURRENT', 1),
  anonMaxTimeoutMs: envInt('CLOUD_ANON_MAX_TIMEOUT_MS', 30_000),
  anonMaxAiCallsPerExecution: envInt('CLOUD_ANON_MAX_AI_CALLS', 2),

  // Global admission control (§25). The box also runs the mainnet node — never starve it.
  globalConcurrency: envInt('CLOUD_GLOBAL_CONCURRENCY', 6),
  perAccountConcurrency: envInt('CLOUD_ACCOUNT_CONCURRENCY', 2),
  queueMaxDepth: envInt('CLOUD_QUEUE_MAX_DEPTH', 500),

  // Per-identity request rate limits (requests/minute).
  rateAnonPerMin: envInt('CLOUD_RATE_ANON_PER_MIN', 10),
  rateUserPerMin: envInt('CLOUD_RATE_USER_PER_MIN', 60),
  rateDeployPerHour: envInt('CLOUD_RATE_DEPLOY_PER_HOUR', 30),
  rateAiGenPerHour: envInt('CLOUD_RATE_AIGEN_PER_HOUR', 20),

  // Secrets + schedules.
  maxSecretBytes: envInt('CLOUD_MAX_SECRET_BYTES', 8 * 1024),
  minScheduleMinutes: envInt('CLOUD_MIN_SCHEDULE_MINUTES', 5),

  // Fleet dispatch.
  jobLeaseSeconds: envInt('CLOUD_JOB_LEASE_SECONDS', 300),
  jobMaxAttempts: envInt('CLOUD_JOB_MAX_ATTEMPTS', 3),
  providerStaleSeconds: envInt('CLOUD_PROVIDER_STALE_SECONDS', 120),
};

// ---------------------------------------------------------------------------
// Bootstrap economics. The ACTIVE PricingPolicy row wins at runtime; these seed it and are
// the fallback when the table is empty.
// ---------------------------------------------------------------------------

export const economics = {
  baseCallNanm: envBig('CLOUD_BASE_CALL_NANM', 100_000n), // 0.0001 ANM
  cpuMsNanm: envBig('CLOUD_CPU_MS_NANM', 20n),
  memMbMsNanm: envBig('CLOUD_MEM_MB_MS_NANM', 1n),
  aiTokenInNanm: envBig('CLOUD_AI_TOKEN_IN_NANM', 1_000n),
  aiTokenOutNanm: envBig('CLOUD_AI_TOKEN_OUT_NANM', 3_000n),
  egressKbNanm: envBig('CLOUD_EGRESS_KB_NANM', 50n),
  gpuMsNanm: envBig('CLOUD_GPU_MS_NANM', 400n),

  // Splits. platformFeeBps + providerShareBps must be <= 10000 (§89).
  platformFeeBps: envInt('CLOUD_PLATFORM_FEE_BPS', 2000), // 20% Animica take rate (§88)
  providerShareBps: envInt('CLOUD_PROVIDER_SHARE_BPS', 1000), // 10% when the fleet ran it

  // Internal unit costs (§74) — what a unit actually costs Animica, in nANM-equivalent.
  costCpuMsNanm: envBig('CLOUD_COST_CPU_MS_NANM', 6n),
  costMemMbMsNanm: envBig('CLOUD_COST_MEM_MB_MS_NANM', 1n),
  costAiTokenNanm: envBig('CLOUD_COST_AI_TOKEN_NANM', 400n),
  costEgressKbNanm: envBig('CLOUD_COST_EGRESS_KB_NANM', 10n),
  costPerCallNanm: envBig('CLOUD_COST_PER_CALL_NANM', 20_000n),

  targetMarginBps: envInt('CLOUD_TARGET_MARGIN_BPS', 6000), // 60% (§76)
  enforceMinMargin: envBool('CLOUD_ENFORCE_MIN_MARGIN', true),

  // Free tier (§35, §78).
  freeExecutionsPerDay: envInt('CLOUD_FREE_EXECUTIONS_PER_DAY', 50),
  freeExecutionsPerMonth: envInt('CLOUD_FREE_EXECUTIONS_PER_MONTH', 500),
  freeAiTokensPerDay: envInt('CLOUD_FREE_AI_TOKENS_PER_DAY', 20_000),
  freeTierMonthlyCeilingNanm: envBig('CLOUD_FREE_TIER_CEILING_NANM', 0n),

  anmUsdFloorMicros: envBig('CLOUD_ANM_USD_FLOOR_MICROS', 0n),
};

// ---------------------------------------------------------------------------
// Capabilities (§19). A function may only request what its owner declared, and a caller must
// authorize anything sensitive before it runs.
// ---------------------------------------------------------------------------

export const CAPABILITIES = [
  'AI_INFERENCE',
  'CALL_FUNCTION',
  'CALL_APP',
  'READ_CHAIN',
  'SPEND_ANM',
  'PERSIST_STATE',
  'SCHEDULE',
  'HTTP_FETCH',
] as const;
export type Capability = (typeof CAPABILITIES)[number];

// Capabilities that always require an explicit, revocable user grant.
export const SENSITIVE_CAPABILITIES: Capability[] = ['SPEND_ANM', 'CALL_APP', 'CALL_FUNCTION', 'HTTP_FETCH'];

export function isCapability(v: string): v is Capability {
  return (CAPABILITIES as readonly string[]).includes(v);
}

// ---------------------------------------------------------------------------
// Commercial plans (§69). Prices are configuration; the DB Plan table is authoritative for
// checkout and may override any limit via limitsJson without a deploy.
// ---------------------------------------------------------------------------

// MUST list every plan key the `Plan` table actually sells, because entitlements are
// resolved BY KEY with a fallback to `free` (`PLAN_ENTITLEMENTS[key] ?? PLAN_ENTITLEMENTS.free`
// in lib/cloud/entitlements.ts). The ladder was renamed to
// free/starter/pro/operator/business, but this list still said `developer` and had no
// `operator` at all — so BOTH `starter` ($4.99) and `operator` ($39.99) missed the map
// and silently resolved to FREE entitlements: api_rate_limit 30 and 0 team members,
// while `pro` at $14.99 got 600 and 10. Two of the four paid tiers were selling limits
// they did not grant, and the more expensive one granted less than the cheaper one.
// `developer` stays as a legacy alias so any historical row still resolves.
export const CLOUD_PLAN_KEYS = ['free', 'starter', 'developer', 'pro', 'operator', 'business', 'enterprise'] as const;
export type CloudPlanKey = (typeof CLOUD_PLAN_KEYS)[number];

export interface Entitlements {
  max_apps: number;
  max_functions: number;
  max_agents: number;
  max_deployments_per_day: number;
  monthly_executions: number;
  monthly_compute_units: number; // 1 unit = 1 CPU-second
  monthly_ai_units: number; // 1 unit = 1000 AI tokens
  max_concurrency: number;
  max_schedules: number;
  min_schedule_minutes: number;
  max_secrets: number;
  log_retention_days: number;
  api_rate_limit: number; // requests/min per key
  priority_class: number; // higher runs first
  marketplace_publishing: boolean;
  premium_analytics: boolean;
  overage_allowed: boolean;
  private_deployments: boolean;
  support: string;
}

// -1 means "no numeric cap from the plan" — the hard safety ceilings in `limits` still apply,
// and every expensive resource still meters + bills (§96: nothing here is truly unlimited).
export const PLAN_ENTITLEMENTS: Record<CloudPlanKey, Entitlements> = {
  free: {
    max_apps: 1,
    max_functions: 3,
    max_agents: 1,
    max_deployments_per_day: 10,
    monthly_executions: economics.freeExecutionsPerMonth,
    monthly_compute_units: 300,
    monthly_ai_units: 200,
    max_concurrency: 1,
    max_schedules: 1,
    min_schedule_minutes: 60,
    max_secrets: 3,
    log_retention_days: 3,
    api_rate_limit: 30,
    priority_class: 0,
    marketplace_publishing: false,
    premium_analytics: false,
    overage_allowed: false,
    private_deployments: false,
    support: 'community',
  },
  // `starter` IS the rung this file used to call `developer` — same numbers, the
  // marketed name changed. Spelled out rather than aliased so the
  // Record<CloudPlanKey, Entitlements> type keeps forcing every key to exist.
  starter: {
    max_apps: 10,
    max_functions: 50,
    max_agents: 10,
    max_deployments_per_day: 100,
    monthly_executions: 100_000,
    monthly_compute_units: 20_000,
    monthly_ai_units: 5_000,
    max_concurrency: 4,
    max_schedules: 20,
    min_schedule_minutes: 15,
    max_secrets: 25,
    log_retention_days: 14,
    api_rate_limit: 120,
    priority_class: 1,
    marketplace_publishing: true,
    premium_analytics: false,
    overage_allowed: true,
    private_deployments: false,
    support: 'email',
  },
  developer: {
    max_apps: 10,
    max_functions: 50,
    max_agents: 10,
    max_deployments_per_day: 100,
    monthly_executions: 100_000,
    monthly_compute_units: 20_000,
    monthly_ai_units: 5_000,
    max_concurrency: 4,
    max_schedules: 20,
    min_schedule_minutes: 15,
    max_secrets: 25,
    log_retention_days: 14,
    api_rate_limit: 120,
    priority_class: 1,
    marketplace_publishing: true,
    premium_analytics: false,
    overage_allowed: true,
    private_deployments: false,
    support: 'email',
  },
  pro: {
    max_apps: 50,
    max_functions: 250,
    max_agents: 50,
    max_deployments_per_day: 500,
    monthly_executions: 1_000_000,
    monthly_compute_units: 150_000,
    monthly_ai_units: 40_000,
    max_concurrency: 12,
    max_schedules: 100,
    min_schedule_minutes: 5,
    max_secrets: 100,
    log_retention_days: 30,
    api_rate_limit: 600,
    priority_class: 2,
    marketplace_publishing: true,
    premium_analytics: true,
    overage_allowed: true,
    private_deployments: false,
    support: 'priority email',
  },
  // INTERIM — deliberately a copy of `pro`, with NO invented quotas.
  //
  // `operator` sells at $39.99, above pro's $14.99, and it was resolving to FREE.
  // The one outcome that must be impossible is a dearer tier granting less than a
  // cheaper one, and matching pro guarantees that without this file inventing
  // numbers nobody decided. The real Operator quotas are a pricing decision —
  // its marketed features (20 Workers, 10,000 scheduled executions/month, 50 .anm
  // deployments, multiple API keys, 10 team members) imply values ABOVE pro, so
  // these should be raised deliberately rather than left at parity.
  operator: {
    max_apps: 50,
    max_functions: 250,
    max_agents: 50,
    max_deployments_per_day: 500,
    monthly_executions: 1_000_000,
    monthly_compute_units: 150_000,
    monthly_ai_units: 40_000,
    max_concurrency: 12,
    max_schedules: 100,
    min_schedule_minutes: 5,
    max_secrets: 100,
    log_retention_days: 30,
    api_rate_limit: 600,
    priority_class: 2,
    marketplace_publishing: true,
    premium_analytics: true,
    overage_allowed: true,
    private_deployments: false,
    support: 'priority email',
  },
  business: {
    max_apps: 250,
    max_functions: 1_000,
    max_agents: 250,
    max_deployments_per_day: 2_000,
    monthly_executions: 10_000_000,
    monthly_compute_units: 1_000_000,
    monthly_ai_units: 250_000,
    max_concurrency: 32,
    max_schedules: 500,
    min_schedule_minutes: 5,
    max_secrets: 500,
    log_retention_days: 90,
    api_rate_limit: 2_400,
    priority_class: 3,
    marketplace_publishing: true,
    premium_analytics: true,
    overage_allowed: true,
    private_deployments: true,
    support: 'premium',
  },
  enterprise: {
    max_apps: -1,
    max_functions: -1,
    max_agents: -1,
    max_deployments_per_day: -1,
    monthly_executions: -1,
    monthly_compute_units: -1,
    monthly_ai_units: -1,
    max_concurrency: 128,
    max_schedules: -1,
    min_schedule_minutes: 1,
    max_secrets: -1,
    log_retention_days: 365,
    api_rate_limit: 10_000,
    priority_class: 4,
    marketplace_publishing: true,
    premium_analytics: true,
    overage_allowed: true,
    private_deployments: true,
    support: 'dedicated + SLA',
  },
};

export interface PlanCatalogEntry {
  key: CloudPlanKey;
  name: string;
  tagline: string;
  priceUsdCents: number;
  icon: string;
  featured: boolean;
  sortOrder: number;
  contactSales: boolean;
  features: string[];
}

// Marketing catalog. Prices are configuration (§69) — the DB row is authoritative.
//
// Halved 2026-08-08. The paid tiers now also carry the CLI: `animica chat` is no
// longer a separate $9.99 product, so a subscriber gets unlimited agentic tasks,
// 50 iterations and an 8-agent swarm on the same plan that raises their Cloud
// limits. The free tier keeps a real CLI (10 agentic tasks/day, 2 agents), which
// is the point — the ladder buys scale, not access.
export const CLOUD_PLAN_CATALOG: PlanCatalogEntry[] = [
  {
    key: 'free',
    name: 'Free',
    tagline: 'Deploy your first Python function today.',
    priceUsdCents: 0,
    icon: '🌱',
    featured: false,
    sortOrder: 10,
    contactSales: false,
    features: [
      `${economics.freeExecutionsPerMonth.toLocaleString()} executions/month`,
      '3 functions, 1 app, 1 agent',
      '1 scheduled job (hourly)',
      '3-day logs',
      'Community support',
    ],
  },
  {
    key: 'developer',
    name: 'Developer',
    tagline: 'Ship real apps and start earning ANM.',
    priceUsdCents: envInt('CLOUD_PLAN_DEVELOPER_CENTS', 1450),
    icon: '⚡',
    featured: false,
    sortOrder: 20,
    contactSales: false,
    features: [
      '100,000 executions/month',
      '50 functions, 10 apps, 10 agents',
      'Marketplace publishing + earnings dashboard',
      '20 scheduled jobs (15 min)',
      'Production API keys, 120 req/min',
      '14-day logs · metered overages',
    ],
  },
  {
    key: 'pro',
    name: 'Pro',
    tagline: 'Priority execution and advanced analytics.',
    priceUsdCents: envInt('CLOUD_PLAN_PRO_CENTS', 4950),
    icon: '🚀',
    featured: true,
    sortOrder: 30,
    contactSales: false,
    features: [
      '1,000,000 executions/month',
      '250 functions, 50 apps, 50 agents',
      'Priority execution queue',
      'Advanced analytics + 30-day logs',
      '100 scheduled jobs (5 min)',
      '600 req/min API limit',
    ],
  },
  {
    key: 'business',
    name: 'Business',
    tagline: 'High-volume workloads with premium support.',
    priceUsdCents: envInt('CLOUD_PLAN_BUSINESS_CENTS', 24950),
    icon: '🏢',
    featured: false,
    sortOrder: 40,
    contactSales: false,
    features: [
      '10,000,000 executions/month',
      'Highest priority class · 32 concurrent',
      'Private deployments',
      '90-day log retention',
      'Advanced monitoring',
      'Premium support',
    ],
  },
  {
    key: 'enterprise',
    name: 'Enterprise',
    tagline: 'Dedicated compute, reserved capacity, custom SLA.',
    priceUsdCents: envInt('CLOUD_PLAN_ENTERPRISE_FROM_CENTS', 75000),
    icon: '🛡️',
    featured: false,
    sortOrder: 50,
    contactSales: true,
    features: [
      'Custom quotas and reserved capacity',
      'Dedicated compute + private deployments',
      'Custom SLA and onboarding',
      'Integration support',
      'Volume pricing',
      'Dedicated account team',
    ],
  },
];

// ---------------------------------------------------------------------------
// Founding Developer program. First N accepted developers.
// ---------------------------------------------------------------------------

export const founding = {
  seats: envInt('FOUNDING_DEV_SEATS', 100),
  proMonths: envInt('FOUNDING_DEV_PRO_MONTHS', 3),
  feeBps: envInt('FOUNDING_DEV_FEE_BPS', 1000), // 10% instead of 20%
  feeMonths: envInt('FOUNDING_DEV_FEE_MONTHS', 12),
  creditsNanm: envBig('FOUNDING_DEV_CREDITS_NANM', 25n * NANM_PER_ANM),
  // A featured slot has to be earned: a published app with at least this many real executions.
  featureMinExecutions: envInt('FOUNDING_DEV_FEATURE_MIN_EXECUTIONS', 25),
  autoAccept: envBool('FOUNDING_DEV_AUTO_ACCEPT', true),
};

// ---------------------------------------------------------------------------
// Managed / high-value services (§87). Configurable offerings, not hardcoded promises.
// ---------------------------------------------------------------------------

export interface ManagedOffering {
  key: string;
  name: string;
  blurb: string;
  setupUsdCents: number;
  monthlyUsdCents: number;
  custom: boolean;
}

export const MANAGED_OFFERINGS: ManagedOffering[] = [
  {
    key: 'private-cloud',
    name: 'Private Animica Python Cloud',
    blurb: 'A dedicated Python Cloud deployment on isolated infrastructure.',
    setupUsdCents: envInt('SVC_PRIVATE_CLOUD_SETUP_CENTS', 500000),
    monthlyUsdCents: envInt('SVC_PRIVATE_CLOUD_MONTHLY_CENTS', 150000),
    custom: false,
  },
  {
    key: 'dedicated-ai',
    name: 'Dedicated AI inference',
    blurb: 'Reserved model capacity with guaranteed throughput.',
    setupUsdCents: 0,
    monthlyUsdCents: envInt('SVC_DEDICATED_AI_MONTHLY_CENTS', 150000),
    custom: false,
  },
  {
    key: 'dedicated-compute',
    name: 'Dedicated compute',
    blurb: 'Reserved execution capacity sized to your workload.',
    setupUsdCents: 0,
    monthlyUsdCents: 0,
    custom: true,
  },
  {
    key: 'integration',
    name: 'Enterprise Animica integration',
    blurb: 'Wallet, chain, AI and Python Cloud integrated into your stack.',
    setupUsdCents: envInt('SVC_INTEGRATION_FROM_CENTS', 500000),
    monthlyUsdCents: 0,
    custom: true,
  },
  {
    key: 'custom-build',
    name: 'Custom Python/AI application development',
    blurb: 'We design, build and deploy the application for you.',
    setupUsdCents: envInt('SVC_CUSTOM_BUILD_FROM_CENTS', 250000),
    monthlyUsdCents: 0,
    custom: true,
  },
];

// ---------------------------------------------------------------------------
// Runtime / infrastructure wiring.
// ---------------------------------------------------------------------------

export const runtime = {
  // The sandbox image. Built by scripts/cloud/build-sandbox-image.sh.
  image: env('CLOUD_SANDBOX_IMAGE', 'anm-pycloud-runtime:1'),
  docker: env('CLOUD_DOCKER_BIN', 'docker'),
  // Where artifacts are staged for the container bind-mount (read-only inside).
  workDir: env('CLOUD_WORK_DIR', '/var/lib/animica-pycloud'),
  // Optional: run the executor as this uid:gid inside the container.
  uid: envInt('CLOUD_SANDBOX_UID', 10001),
  gid: envInt('CLOUD_SANDBOX_GID', 10001),
  // Public endpoint base for deployed functions.
  publicBase: env('PUBLIC_BASE_URL', 'https://animica.dev'),
  // Deploy wallet label used to sign on-chain deployment anchors.
  anchorWalletLabel: env('CLOUD_ANCHOR_WALLET_LABEL', 'mldsamain'),
  anchorConfirmations: envInt('CLOUD_ANCHOR_CONFIRMATIONS', 12),
  // ANM/USD price feed written by anm-price.timer (the existing authoritative source).
  anmPriceFile: env('ANM_PRICE_FILE', '/var/www/animica.dev/anm-price.json'),
};

export const NANM = NANM_PER_ANM;
