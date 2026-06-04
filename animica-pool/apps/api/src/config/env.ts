import { z } from "zod";

// Validate critical env at startup; throw in production if missing.
const Schema = z.object({
  NODE_ENV: z.enum(["development", "production", "test"]).default("development"),
  API_PORT: z.coerce.number().int().positive().default(4000),
  DATABASE_URL: z.string().min(1),
  REDIS_URL: z.string().optional().default(""),
  JWT_SECRET: z.string().min(32),
  NEXT_PUBLIC_APP_URL: z.string().default("http://localhost:3000"),
  ADMIN_EMAILS: z.string().optional().default(""),

  // NOWPayments (used from Phase 3+; optional at boot)
  NOWPAYMENTS_API_KEY: z.string().optional().default(""),
  NOWPAYMENTS_IPN_SECRET: z.string().optional().default(""),
  NOWPAYMENTS_EMAIL: z.string().optional().default(""),
  NOWPAYMENTS_PASSWORD: z.string().optional().default(""),
  NOWPAYMENTS_PUBLIC_CALLBACK_URL: z.string().optional().default(""),
  // Comma-separated other IPN webhook URLs to fan unmatched callbacks to
  // (e.g. the buy.animica.org gateway) so one NOWPayments IPN URL serves both.
  NOWPAYMENTS_FORWARD_IPN_URLS: z.string().optional().default(""),
  NOWPAYMENTS_SANDBOX: z.string().optional().default("true").transform((v) => v !== "false"),
  NOWPAYMENTS_PAYOUTS_ENABLED: z.string().optional().default("false").transform((v) => v === "true"),
  AUTO_PAYOUTS_ENABLED: z.string().optional().default("false").transform((v) => v === "true"),
  // Rolling-24h payout ceiling in ANM-equivalent (all assets converted via
  // ANM_USD_PRICE). 0 = unlimited. Over-cap batches are held, not submitted.
  DAILY_PAYOUT_CAP_ANM: z.coerce.number().nonnegative().default(300000),

  // Mining
  ANM_USD_PRICE: z.string().optional().default("0.00125"),
  XMR_USD_PRICE: z.string().optional().default(""),
  POOL_FEE_PERCENT: z.string().optional().default("5"),
  ANM_POOL_API_URL: z.string().optional().default(""),
  XMR_POOL_API_URL: z.string().optional().default(""),
  ANM_POOL_STRATUM_URL: z.string().optional().default("stratum+tcp://pool.animica.org:3333"),
  XMR_POOL_STRATUM_URL: z.string().optional().default("stratum+tcp://pool.animica.org:3333"),
  DUAL_POOL_STRATUM_URL: z.string().optional().default("stratum+tcp://pool.animica.org:3333"),
  MINING_USE_MOCK: z.string().optional().default("false").transform((v) => v === "true"),

  // Redistribution split
  SPLIT_PROVIDERS_PERCENT: z.coerce.number().default(70),
  SPLIT_TREASURY_PERCENT: z.coerce.number().default(20),
  SPLIT_REFERRAL_PERCENT: z.coerce.number().default(10),

  // Providers
  MOCK_PROVIDER_ENABLED: z.string().optional().default("false").transform((v) => v === "true"),
  // Bittensor demand side: Chutes (SN64) by default — gateway overridable.
  BITTENSOR_ENABLED: z.string().optional().default("false").transform((v) => v === "true"),
  BITTENSOR_GATEWAY_URL: z.string().optional().default(""),
  BITTENSOR_API_KEY: z.string().optional().default(""),
  BITTENSOR_DEFAULT_SUBNET: z.string().optional().default("64"),
  BITTENSOR_TIMEOUT_MS: z.coerce.number().default(30000),
  // JSON: { "anm-model": { "upstream": "...", "inUsdPer1M": n, "outUsdPer1M": n } }
  BITTENSOR_MODEL_MAP: z.string().optional().default(""),
  // OpenRouter pinned to the chutes provider — Bittensor fallback path.
  OPENROUTER_ENABLED: z.string().optional().default("false").transform((v) => v === "true"),
  OPENROUTER_API_KEY: z.string().optional().default(""),
  OPENROUTER_API_URL: z.string().optional().default(""),
  OPENROUTER_PIN_PROVIDER: z.string().optional().default("chutes"),
  OPENROUTER_ALLOW_FALLBACKS: z.string().optional().default("true").transform((v) => v !== "false"),
  OPENROUTER_MODEL_MAP: z.string().optional().default(""),
  // Targon (SN4): live but no public rate card — keep off until confirmed.
  TARGON_ENABLED: z.string().optional().default("false").transform((v) => v === "true"),
  TARGON_API_KEY: z.string().optional().default(""),
  TARGON_API_URL: z.string().optional().default(""),
  TARGON_MODEL_MAP: z.string().optional().default(""),
  RUNPOD_ENABLED: z.string().optional().default("false").transform((v) => v === "true"),
  RUNPOD_API_KEY: z.string().optional().default(""),
  RUNPOD_ENDPOINT_ID: z.string().optional().default(""),
  RUNPOD_TIMEOUT_MS: z.coerce.number().default(30000),
  AKASH_ENABLED: z.string().optional().default("false").transform((v) => v === "true"),
  AKASH_API_URL: z.string().optional().default(""),
  AKASH_DEPLOYMENT_ID: z.string().optional().default(""),
  AKASH_TIMEOUT_MS: z.coerce.number().default(30000),
  PROVIDER_TIMEOUT_MS: z.coerce.number().default(30000),
  LAMBDA_ENABLED: z.string().optional().default("false").transform((v) => v === "true"),
  LAMBDA_API_KEY: z.string().optional().default(""),
  LAMBDA_API_URL: z.string().optional().default(""),
  SPHERON_ENABLED: z.string().optional().default("false").transform((v) => v === "true"),
  SPHERON_API_KEY: z.string().optional().default(""),
  SPHERON_API_URL: z.string().optional().default(""),
  HYPERBOLIC_ENABLED: z.string().optional().default("false").transform((v) => v === "true"),
  HYPERBOLIC_API_KEY: z.string().optional().default(""),
  HYPERBOLIC_API_URL: z.string().optional().default(""),
  RENDER_ENABLED: z.string().optional().default("false").transform((v) => v === "true"),
  RENDER_API_KEY: z.string().optional().default(""),
  RENDER_API_URL: z.string().optional().default(""),
  IONET_ENABLED: z.string().optional().default("false").transform((v) => v === "true"),
  IONET_API_KEY: z.string().optional().default(""),
  IONET_API_URL: z.string().optional().default(""),

  // ---- Bittensor supply side (SN51 Celium GPU pool) ----
  // Master pause flag (buy-desk pattern): everything builds + enrolls behind
  // it; nothing is advertised to validators until flipped on.
  BITTENSOR_MINING_ENABLED: z.string().optional().default("false").transform((v) => v === "true"),
  // Target subnet + our miner hotkey (set once registered via btcli).
  BITTENSOR_MINING_NETUID: z.coerce.number().default(51),
  BITTENSOR_MINER_HOTKEY: z.string().optional().default(""),
  // Rig-eligibility gate (slashing protection: SN51 collateral is burned if a
  // rented GPU drops, so only proven-uptime rigs may enroll).
  BITTENSOR_MIN_UPTIME_PCT: z.coerce.number().default(97),
  BITTENSOR_MIN_HISTORY_DAYS: z.coerce.number().default(7),
  BITTENSOR_MIN_VRAM_GB: z.coerce.number().default(16),
  // Rig-owner share of Bittensor earnings (pool keeps the rest) + holdback
  // window mirroring SN51's 7-days-of-fees collateral.
  BITTENSOR_OWNER_SHARE_PERCENT: z.coerce.number().default(70),
  BITTENSOR_HOLDBACK_DAYS: z.coerce.number().default(7),
  // Treasury accumulation target that funds the SN51 UID registration burn +
  // first executor collateral (USD). Progress = treasury slice of net revenue.
  BITTENSOR_REG_TARGET_USD: z.coerce.number().default(2000),
  // TAO price override for earnings conversion ("" = CoinGecko live).
  TAO_USD_PRICE: z.string().optional().default(""),
  // On-chain earnings polling (taostats.io API). Poller is dormant without a
  // key or while the miner hotkey is still the PENDING placeholder.
  TAOSTATS_API_KEY: z.string().optional().default(""),
  TAOSTATS_API_URL: z.string().optional().default("https://api.taostats.io"),
  BITTENSOR_POLL_INTERVAL_MIN: z.coerce.number().default(60),

  // Mining ANM-payout (on-chain via Animica node RPC) + price oracle
  ANIMICA_RPC_URL: z.string().optional().default(""),
  ANIMICA_RPC_USER: z.string().optional().default(""),
  ANIMICA_RPC_PASSWORD: z.string().optional().default(""),
  ANIMICA_PAYOUT_FROM_ADDRESS: z.string().optional().default(""),
  COINGECKO_BASE_URL: z.string().optional().default("https://api.coingecko.com/api/v3"),
});

export type AppEnv = z.infer<typeof Schema>;

let cached: AppEnv | null = null;

export function env(): AppEnv {
  if (cached) return cached;
  const parsed = Schema.safeParse(process.env);
  if (!parsed.success) {
    const issues = parsed.error.issues.map((i) => `${i.path.join(".")}: ${i.message}`).join("\n  ");
    if (process.env.NODE_ENV === "production") {
      throw new Error(`Invalid env configuration:\n  ${issues}`);
    }
    // eslint-disable-next-line no-console
    console.warn(`[env] incomplete configuration (dev):\n  ${issues}`);
    cached = Schema.partial().parse(process.env) as AppEnv;
    return cached;
  }
  cached = parsed.data;
  return cached;
}

import { type ProviderRouterConfig, type ProviderModelMap, DEFAULT_PROVIDER_URLS } from "@animica/provider-router";

function parseModelMap(raw: string): ProviderModelMap | undefined {
  if (!raw) return undefined;
  try {
    return JSON.parse(raw) as ProviderModelMap;
  } catch {
    // eslint-disable-next-line no-console
    console.warn("[env] ignoring invalid model-map JSON");
    return undefined;
  }
}

export function providerConfig(): ProviderRouterConfig {
  const e = env();
  const t = e.PROVIDER_TIMEOUT_MS;
  return {
    mock: { enabled: e.MOCK_PROVIDER_ENABLED },
    bittensor: {
      enabled: e.BITTENSOR_ENABLED, gatewayUrl: e.BITTENSOR_GATEWAY_URL || DEFAULT_PROVIDER_URLS.bittensor,
      apiKey: e.BITTENSOR_API_KEY, defaultSubnet: e.BITTENSOR_DEFAULT_SUBNET, timeoutMs: e.BITTENSOR_TIMEOUT_MS,
      modelMap: parseModelMap(e.BITTENSOR_MODEL_MAP),
    },
    openrouter: {
      enabled: e.OPENROUTER_ENABLED, apiKey: e.OPENROUTER_API_KEY,
      baseUrl: e.OPENROUTER_API_URL || DEFAULT_PROVIDER_URLS.openrouter, timeoutMs: t,
      pinProvider: e.OPENROUTER_PIN_PROVIDER, allowFallbacks: e.OPENROUTER_ALLOW_FALLBACKS,
      modelMap: parseModelMap(e.OPENROUTER_MODEL_MAP),
    },
    targon: {
      enabled: e.TARGON_ENABLED, apiKey: e.TARGON_API_KEY,
      baseUrl: e.TARGON_API_URL || DEFAULT_PROVIDER_URLS.targon, timeoutMs: t,
      modelMap: parseModelMap(e.TARGON_MODEL_MAP),
    },
    runpod: { enabled: e.RUNPOD_ENABLED, apiKey: e.RUNPOD_API_KEY, endpointId: e.RUNPOD_ENDPOINT_ID, timeoutMs: e.RUNPOD_TIMEOUT_MS },
    akash: { enabled: e.AKASH_ENABLED, apiUrl: e.AKASH_API_URL, deploymentId: e.AKASH_DEPLOYMENT_ID, timeoutMs: e.AKASH_TIMEOUT_MS },
    lambda: { enabled: e.LAMBDA_ENABLED, apiKey: e.LAMBDA_API_KEY, baseUrl: e.LAMBDA_API_URL || DEFAULT_PROVIDER_URLS.lambda, timeoutMs: t },
    spheron: { enabled: e.SPHERON_ENABLED, apiKey: e.SPHERON_API_KEY, baseUrl: e.SPHERON_API_URL || DEFAULT_PROVIDER_URLS.spheron, timeoutMs: t },
    hyperbolic: { enabled: e.HYPERBOLIC_ENABLED, apiKey: e.HYPERBOLIC_API_KEY, baseUrl: e.HYPERBOLIC_API_URL || DEFAULT_PROVIDER_URLS.hyperbolic, timeoutMs: t },
    render: { enabled: e.RENDER_ENABLED, apiKey: e.RENDER_API_KEY, baseUrl: e.RENDER_API_URL || DEFAULT_PROVIDER_URLS.render, timeoutMs: t },
    ionet: { enabled: e.IONET_ENABLED, apiKey: e.IONET_API_KEY, baseUrl: e.IONET_API_URL || DEFAULT_PROVIDER_URLS.ionet, timeoutMs: t },
  };
}

export function adminEmails(): Set<string> {
  return new Set(
    env().ADMIN_EMAILS.split(",").map((s) => s.trim().toLowerCase()).filter(Boolean),
  );
}
