interface HttpProvider {
  enabled: boolean;
  apiKey: string;
  baseUrl: string;
  timeoutMs: number;
}

/** anm-* catalog model → a provider's upstream model id + static wholesale
 *  prices (USD per 1M tokens) used until/unless a live price feed is warm. */
export interface MappedModel {
  upstream: string;
  inUsdPer1M: number;
  outUsdPer1M: number;
}
export type ProviderModelMap = Record<string, MappedModel>;

export interface ProviderRouterConfig {
  mock: { enabled: boolean };
  // Bittensor demand side = Chutes (SN64) OpenAI-compatible API by default.
  bittensor: {
    enabled: boolean; gatewayUrl: string; apiKey: string; defaultSubnet: string; timeoutMs: number;
    modelMap?: ProviderModelMap;
  };
  // OpenRouter pinned to the `chutes` provider — same Bittensor backend with
  // fiat billing; serves as the Bittensor fallback path.
  openrouter: {
    enabled: boolean; apiKey: string; baseUrl: string; timeoutMs: number;
    pinProvider: string; allowFallbacks: boolean; modelMap?: ProviderModelMap;
  };
  // Targon (SN4) — live but auth-gated and repositioning; off by default.
  targon: HttpProvider & { modelMap?: ProviderModelMap };
  runpod: { enabled: boolean; apiKey: string; endpointId: string; timeoutMs: number };
  akash: { enabled: boolean; apiUrl: string; deploymentId: string; timeoutMs: number };
  lambda: HttpProvider;
  spheron: HttpProvider;
  hyperbolic: HttpProvider;
  render: HttpProvider;
  ionet: HttpProvider;
}

// Sensible default OpenAI-compatible base URLs (overridable via *_API_URL env).
export const DEFAULT_PROVIDER_URLS: Record<string, string> = {
  bittensor: "https://llm.chutes.ai/v1", // Chutes (SN64) — public pricing at /models (verified 2026-06-04)
  openrouter: "https://openrouter.ai/api/v1",
  targon: "https://api.targon.com/v1",
  lambda: "https://api.lambda.ai/v1",
  hyperbolic: "https://api.hyperbolic.xyz/v1",
  ionet: "https://api.intelligence.io.solutions/api/v1",
  spheron: "https://api.spheron.network/v1",
  render: "", // no public inference API — must set RENDER_API_URL explicitly
};

// Default anm-* → Chutes model maps. Static prices are the live Chutes rates
// observed 2026-06-04 (USD per 1M tokens); the adapter's live price feed
// overrides them once warm. Customer prices live in MODEL_PRICING_USD_PER_1K
// (e.g. anm-pro-70b $1.50/$3.00 per 1M vs $0.28/$0.42 cost ≈ 81–86% margin).
export const CHUTES_DEFAULT_MODEL_MAP: ProviderModelMap = {
  "anm-fast-8b": { upstream: "unsloth/Mistral-Nemo-Instruct-2407-TEE", inUsdPer1M: 0.0245, outUsdPer1M: 0.0978 },
  "anm-code-7b": { upstream: "Qwen/Qwen3-32B-TEE", inUsdPer1M: 0.104, outUsdPer1M: 0.416 },
  "anm-pro-70b": { upstream: "deepseek-ai/DeepSeek-V3.2-TEE", inUsdPer1M: 0.28, outUsdPer1M: 0.42 },
  "anm-bittensor-router": { upstream: "deepseek-ai/DeepSeek-V3.2-TEE", inUsdPer1M: 0.28, outUsdPer1M: 0.42 },
};

// OpenRouter ids verified against the live /models list 2026-06-04. Static
// prices are OpenRouter's published rates (slightly above Chutes direct).
export const OPENROUTER_DEFAULT_MODEL_MAP: ProviderModelMap = {
  "anm-fast-8b": { upstream: "mistralai/mistral-nemo", inUsdPer1M: 0.03, outUsdPer1M: 0.11 },
  "anm-code-7b": { upstream: "qwen/qwen3.6-27b", inUsdPer1M: 0.3, outUsdPer1M: 2.0 },
  "anm-pro-70b": { upstream: "deepseek/deepseek-v3.2", inUsdPer1M: 0.23, outUsdPer1M: 0.35 },
  "anm-bittensor-router": { upstream: "deepseek/deepseek-v3.2", inUsdPer1M: 0.23, outUsdPer1M: 0.35 },
};

// Targon publishes no public rate card — statics are deliberately conservative
// (overestimate cost) so recorded margin can only surprise upward.
export const TARGON_DEFAULT_MODEL_MAP: ProviderModelMap = {
  "anm-pro-70b": { upstream: "deepseek-ai/DeepSeek-V3", inUsdPer1M: 0.5, outUsdPer1M: 1.5 },
  "anm-bittensor-router": { upstream: "deepseek-ai/DeepSeek-V3", inUsdPer1M: 0.5, outUsdPer1M: 1.5 },
};

// Lower number = tried first. Per spec: Bittensor (Chutes direct) → OpenRouter
// (chutes-pinned) → Targon → RunPod → Akash → others → Animica workers → mock
// (mock only when it's the only thing enabled, i.e. dev).
export const PROVIDER_PRIORITY: Record<string, number> = {
  bittensor: 10,
  openrouter: 15,
  targon: 18,
  runpod: 20,
  akash: 30,
  lambda: 40,
  spheron: 50,
  hyperbolic: 60,
  render: 70,
  ionet: 80,
  animica_worker: 90,
  mock: 1000,
};
