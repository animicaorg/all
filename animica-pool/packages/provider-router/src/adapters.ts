import type {
  ProviderAdapter,
  InferenceRequest,
  ProviderHealthResult,
  ProviderCostEstimate,
  ProviderInferenceResult,
  ProviderEmbeddingResult,
} from "@animica/shared";
import { estimateTokens, requestInputText, withTimeout } from "./util.js";
import {
  type ProviderRouterConfig,
  type ProviderModelMap,
  CHUTES_DEFAULT_MODEL_MAP,
  OPENROUTER_DEFAULT_MODEL_MAP,
  TARGON_DEFAULT_MODEL_MAP,
} from "./config.js";

// Provider USD cost per 1K tokens (our wholesale cost; customer price lives in
// shared MODEL_PRICING_USD_PER_1K). Kept conservative for margin calc.
const PROVIDER_RATE = { in: 0.00008, out: 0.00024 };

function costUsd(inTok: number, outTok: number): number {
  return (inTok / 1000) * PROVIDER_RATE.in + (outTok / 1000) * PROVIDER_RATE.out;
}

// -------------------- Live price feed --------------------
/** Polls an OpenAI-style /models listing for per-model pricing so recorded
 *  providerCostUsd tracks reality instead of a static guess. Lookups are sync
 *  (from cache); a stale cache triggers a background refresh. */
type PriceParser = (json: any) => Map<string, { inUsdPer1M: number; outUsdPer1M: number }>;

class LivePriceFeed {
  private prices = new Map<string, { inUsdPer1M: number; outUsdPer1M: number }>();
  private fetchedAt = 0;
  private inflight: Promise<void> | null = null;

  constructor(
    private readonly url: () => string,
    private readonly headers: () => Record<string, string>,
    private readonly parse: PriceParser,
    private readonly ttlMs = 10 * 60 * 1000,
  ) {}

  get(upstreamModel: string): { inUsdPer1M: number; outUsdPer1M: number } | undefined {
    if (Date.now() - this.fetchedAt > this.ttlMs) this.refresh();
    return this.prices.get(upstreamModel);
  }

  private refresh() {
    if (this.inflight) return;
    this.inflight = (async () => {
      try {
        const res = await withTimeout(fetch(`${this.url()}/models`, { headers: this.headers() }), 8000, "price-feed");
        if (!res.ok) return;
        const parsed = this.parse(await res.json());
        if (parsed.size > 0) {
          this.prices = parsed;
          this.fetchedAt = Date.now();
        }
      } catch {
        /* keep stale/static prices */
      } finally {
        this.inflight = null;
      }
    })();
  }
}

// Chutes /v1/models: pricing.prompt / pricing.completion are numbers in USD
// per 1M tokens (verified live 2026-06-04).
const parseChutesPrices: PriceParser = (json) => {
  const out = new Map<string, { inUsdPer1M: number; outUsdPer1M: number }>();
  for (const m of json?.data ?? []) {
    const p = m?.pricing;
    if (m?.id && typeof p?.prompt === "number" && typeof p?.completion === "number") {
      out.set(m.id, { inUsdPer1M: p.prompt, outUsdPer1M: p.completion });
    }
  }
  return out;
};

// OpenRouter /api/v1/models: pricing.prompt / pricing.completion are strings
// in USD per single token.
const parseOpenRouterPrices: PriceParser = (json) => {
  const out = new Map<string, { inUsdPer1M: number; outUsdPer1M: number }>();
  for (const m of json?.data ?? []) {
    const inTok = Number(m?.pricing?.prompt);
    const outTok = Number(m?.pricing?.completion);
    if (m?.id && Number.isFinite(inTok) && Number.isFinite(outTok)) {
      out.set(m.id, { inUsdPer1M: inTok * 1_000_000, outUsdPer1M: outTok * 1_000_000 });
    }
  }
  return out;
};

// -------------------- Mock (dev only) --------------------
export class MockProvider implements ProviderAdapter {
  name = "mock";
  constructor(private cfg: ProviderRouterConfig["mock"]) {}
  async isEnabled() {
    return this.cfg.enabled;
  }
  async healthCheck(): Promise<ProviderHealthResult> {
    return { provider: this.name, healthy: this.cfg.enabled, latencyMs: 1 };
  }
  async estimateCost(req: InferenceRequest): Promise<ProviderCostEstimate> {
    const inTok = estimateTokens(requestInputText(req));
    const outTok = Math.min(req.maxTokens ?? 256, 256);
    return { provider: this.name, estimatedCostUsd: 0, estimatedInputTokens: inTok, estimatedOutputTokens: outTok };
  }
  async runChatCompletion(req: InferenceRequest): Promise<ProviderInferenceResult> {
    const input = requestInputText(req);
    const inTok = estimateTokens(input);
    const output = `『mock:${req.model}』 ${input.slice(0, 200)}`;
    const outTok = estimateTokens(output);
    return {
      provider: this.name, model: req.model, requestId: req.requestId, status: "success",
      inputTokens: inTok, outputTokens: outTok, providerCostUsd: 0, latencyMs: 2, output,
    };
  }
  async runEmbedding(req: InferenceRequest): Promise<ProviderEmbeddingResult> {
    const inputs = Array.isArray(req.input) ? req.input : [requestInputText(req)];
    const inTok = inputs.reduce((s, t) => s + estimateTokens(t), 0);
    const embeddings = inputs.map((t) => Array.from({ length: 8 }, (_, i) => ((t.length + i) % 17) / 17));
    return { provider: this.name, model: req.model, requestId: req.requestId, status: "success", inputTokens: inTok, providerCostUsd: 0, latencyMs: 2, embeddings };
  }
}

// -------------------- OpenAI-compatible HTTP base --------------------
abstract class OpenAiCompatibleProvider implements ProviderAdapter {
  abstract name: string;
  protected abstract baseUrl(): string;
  protected abstract apiKey(): string;
  protected abstract timeoutMs(): number;
  protected abstract enabled(): boolean;

  async isEnabled() {
    return this.enabled() && !!this.baseUrl();
  }
  async healthCheck(): Promise<ProviderHealthResult> {
    if (!(await this.isEnabled())) return { provider: this.name, healthy: false, error: "disabled" };
    const start = Date.now();
    try {
      const res = await withTimeout(
        fetch(`${this.baseUrl()}/models`, { headers: this.headers() }),
        Math.min(this.timeoutMs(), 8000),
        `${this.name}.health`,
      );
      return { provider: this.name, healthy: res.ok, latencyMs: Date.now() - start, error: res.ok ? undefined : `HTTP ${res.status}` };
    } catch (e) {
      return { provider: this.name, healthy: false, latencyMs: Date.now() - start, error: String(e) };
    }
  }
  async estimateCost(req: InferenceRequest): Promise<ProviderCostEstimate> {
    const inTok = estimateTokens(requestInputText(req));
    const outTok = Math.min(req.maxTokens ?? 256, 1024);
    return { provider: this.name, estimatedCostUsd: costUsd(inTok, outTok), estimatedInputTokens: inTok, estimatedOutputTokens: outTok };
  }
  protected headers(): Record<string, string> {
    return { "content-type": "application/json", authorization: `Bearer ${this.apiKey()}` };
  }
  /** Map an anm-* catalog model to this provider's upstream model id. Return
   *  null to decline the request — the router falls through to the next
   *  provider (e.g. anm-worker-* models stay on the Animica worker pool). */
  protected upstreamModel(model: string): string | null {
    return model;
  }
  /** Wholesale cost for margin accounting. Default = static PROVIDER_RATE;
   *  providers with a live price feed or model map override this. */
  protected providerCostUsd(upstreamModel: string, inTok: number, outTok: number): number {
    void upstreamModel;
    return costUsd(inTok, outTok);
  }
  /** Extra body fields merged into the upstream request (e.g. OpenRouter
   *  provider pinning). */
  protected extraBody(): Record<string, unknown> {
    return {};
  }
  async runChatCompletion(req: InferenceRequest): Promise<ProviderInferenceResult> {
    const upstream = this.upstreamModel(req.model);
    if (!upstream) throw new Error(`${this.name}: no upstream mapping for model '${req.model}'`);
    const start = Date.now();
    const res = await withTimeout(
      fetch(`${this.baseUrl()}/chat/completions`, {
        method: "POST",
        headers: this.headers(),
        body: JSON.stringify({
          model: upstream,
          messages: req.messages ?? [{ role: "user", content: requestInputText(req) }],
          temperature: req.temperature ?? 0.7,
          max_tokens: req.maxTokens ?? 512,
          stream: false,
          ...this.extraBody(),
        }),
      }),
      this.timeoutMs(),
      `${this.name}.chat`,
    );
    if (!res.ok) throw new Error(`${this.name} HTTP ${res.status}: ${(await res.text()).slice(0, 200)}`);
    const json: any = await res.json();
    const output = json?.choices?.[0]?.message?.content ?? "";
    const inTok = json?.usage?.prompt_tokens ?? estimateTokens(requestInputText(req));
    const outTok = json?.usage?.completion_tokens ?? estimateTokens(output);
    return {
      provider: this.name, model: req.model, requestId: req.requestId, status: "success",
      inputTokens: inTok, outputTokens: outTok, providerCostUsd: this.providerCostUsd(upstream, inTok, outTok),
      latencyMs: Date.now() - start, output,
    };
  }
}

/** Shared cost logic for providers with a static model map + optional live
 *  price feed: live price wins, then the map's static rates, then the
 *  conservative default. */
function mappedCostUsd(
  map: ProviderModelMap,
  feed: LivePriceFeed | null,
  upstreamModel: string,
  inTok: number,
  outTok: number,
): number {
  const live = feed?.get(upstreamModel);
  const entry = live ?? Object.values(map).find((m) => m.upstream === upstreamModel);
  if (!entry) return costUsd(inTok, outTok);
  return (inTok / 1_000_000) * entry.inUsdPer1M + (outTok / 1_000_000) * entry.outUsdPer1M;
}

// -------------------- Bittensor (first-class) --------------------
// Demand side of the Bittensor positioning: buy emission-subsidized inference
// from Chutes (SN64, https://llm.chutes.ai/v1 — the default gateway) and
// resell it at anm-* catalog prices. Margin = customer price (shared
// MODEL_PRICING_USD_PER_1K) minus the live Chutes rate recorded here.
// GATEWAY_URL stays overridable for a future Animica subnet or local gateway.
export class BittensorProvider extends OpenAiCompatibleProvider {
  name = "bittensor";
  private map: ProviderModelMap;
  private feed: LivePriceFeed;
  constructor(private cfg: ProviderRouterConfig["bittensor"]) {
    super();
    this.map = { ...CHUTES_DEFAULT_MODEL_MAP, ...(cfg.modelMap ?? {}) };
    this.feed = new LivePriceFeed(() => this.baseUrl(), () => this.headers(), parseChutesPrices);
  }
  protected baseUrl() {
    return this.cfg.gatewayUrl.replace(/\/$/, "");
  }
  protected apiKey() {
    return this.cfg.apiKey;
  }
  protected timeoutMs() {
    return this.cfg.timeoutMs;
  }
  protected enabled() {
    return this.cfg.enabled && !!this.cfg.apiKey;
  }
  protected upstreamModel(model: string): string | null {
    return this.map[model]?.upstream ?? null;
  }
  protected providerCostUsd(upstreamModel: string, inTok: number, outTok: number): number {
    return mappedCostUsd(this.map, this.feed, upstreamModel, inTok, outTok);
  }
  // subnet routing hook: anm-bittensor-router maps to the defaultSubnet's API
  // (Chutes/SN64 today; per-task subnet selection lands with more gateways).
}

// -------------------- OpenRouter (Bittensor fallback) --------------------
// Same Chutes backend reached through OpenRouter's fiat-billed aggregator —
// pinned via provider.order so the request still lands on Bittensor miners.
// allowFallbacks=true keeps reliability when Chutes itself is down (margin is
// preserved either way; cost tracks OpenRouter's live per-token price feed).
export class OpenRouterProvider extends OpenAiCompatibleProvider {
  name = "openrouter";
  private map: ProviderModelMap;
  private feed: LivePriceFeed;
  constructor(private cfg: ProviderRouterConfig["openrouter"]) {
    super();
    this.map = { ...OPENROUTER_DEFAULT_MODEL_MAP, ...(cfg.modelMap ?? {}) };
    this.feed = new LivePriceFeed(() => this.baseUrl(), () => this.headers(), parseOpenRouterPrices);
  }
  protected baseUrl() {
    return this.cfg.baseUrl.replace(/\/$/, "");
  }
  protected apiKey() {
    return this.cfg.apiKey;
  }
  protected timeoutMs() {
    return this.cfg.timeoutMs;
  }
  protected enabled() {
    return this.cfg.enabled && !!this.cfg.apiKey;
  }
  protected upstreamModel(model: string): string | null {
    return this.map[model]?.upstream ?? null;
  }
  protected providerCostUsd(upstreamModel: string, inTok: number, outTok: number): number {
    return mappedCostUsd(this.map, this.feed, upstreamModel, inTok, outTok);
  }
  protected extraBody(): Record<string, unknown> {
    if (!this.cfg.pinProvider) return {};
    return { provider: { order: [this.cfg.pinProvider], allow_fallbacks: this.cfg.allowFallbacks } };
  }
}

// -------------------- Targon (SN4, flagged fallback) --------------------
// Live OpenAI-compatible surface at api.targon.com/v1 but no public rate card
// and the subnet is repositioning — keep behind TARGON_ENABLED until pricing
// is confirmed from the dashboard. Static cost map deliberately overestimates.
export class TargonProvider extends OpenAiCompatibleProvider {
  name = "targon";
  private map: ProviderModelMap;
  constructor(private cfg: ProviderRouterConfig["targon"]) {
    super();
    this.map = { ...TARGON_DEFAULT_MODEL_MAP, ...(cfg.modelMap ?? {}) };
  }
  protected baseUrl() {
    return (this.cfg.baseUrl || "").replace(/\/$/, "");
  }
  protected apiKey() {
    return this.cfg.apiKey;
  }
  protected timeoutMs() {
    return this.cfg.timeoutMs;
  }
  protected enabled() {
    return this.cfg.enabled && !!this.cfg.baseUrl && !!this.cfg.apiKey;
  }
  protected upstreamModel(model: string): string | null {
    return this.map[model]?.upstream ?? null;
  }
  protected providerCostUsd(upstreamModel: string, inTok: number, outTok: number): number {
    return mappedCostUsd(this.map, null, upstreamModel, inTok, outTok);
  }
}

// -------------------- RunPod (serverless OpenAI-compatible) --------------------
export class RunPodProvider extends OpenAiCompatibleProvider {
  name = "runpod";
  constructor(private cfg: ProviderRouterConfig["runpod"]) {
    super();
  }
  protected baseUrl() {
    // RunPod serverless OpenAI-compatible endpoint shape.
    return this.cfg.endpointId ? `https://api.runpod.ai/v2/${this.cfg.endpointId}/openai/v1` : "";
  }
  protected apiKey() {
    return this.cfg.apiKey;
  }
  protected timeoutMs() {
    return this.cfg.timeoutMs;
  }
  protected enabled() {
    return this.cfg.enabled && !!this.cfg.endpointId;
  }
}

// -------------------- Akash (decentralized GPU) --------------------
export class AkashProvider extends OpenAiCompatibleProvider {
  name = "akash";
  constructor(private cfg: ProviderRouterConfig["akash"]) {
    super();
  }
  protected baseUrl() {
    return (this.cfg.apiUrl || "").replace(/\/$/, "");
  }
  protected apiKey() {
    return "";
  }
  protected timeoutMs() {
    return this.cfg.timeoutMs;
  }
  protected enabled() {
    return this.cfg.enabled && !!this.cfg.apiUrl;
  }
}

// -------------------- Generic OpenAI-compatible HTTP provider --------------------
// Used for Lambda, Hyperbolic, io.net, Spheron, Render — each is an
// OpenAI-compatible inference endpoint differing only by base URL + key.
export interface HttpProviderConfig {
  enabled: boolean;
  apiKey: string;
  baseUrl: string;
  timeoutMs: number;
}

export class HttpOpenAiProvider extends OpenAiCompatibleProvider {
  name: string;
  constructor(name: string, private cfg: HttpProviderConfig) {
    super();
    this.name = name;
  }
  protected baseUrl() {
    return (this.cfg.baseUrl || "").replace(/\/$/, "");
  }
  protected apiKey() {
    return this.cfg.apiKey;
  }
  protected timeoutMs() {
    return this.cfg.timeoutMs;
  }
  protected enabled() {
    return this.cfg.enabled && !!this.cfg.baseUrl && !!this.cfg.apiKey;
  }
}
