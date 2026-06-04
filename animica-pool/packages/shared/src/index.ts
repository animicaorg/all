// Shared types, enums, and constants across web + api + provider-router.

export type UserRole = "GUEST" | "CUSTOMER" | "MINER" | "WORKER" | "ADMIN";
export type MiningMode = "ANM_ONLY" | "XMR_ONLY" | "DUAL_50_50";
export type PayoutAsset =
  | "ANM" | "XMR" | "SOL" | "USDT" | "BTC" | "SPLIT_ANM_XMR" | "ORIGINAL_ASSET";

export const MODELS = [
  "anm-fast-8b",
  "anm-code-7b",
  "anm-pro-70b",
  "anm-bittensor-router",
  "anm-embed",
  "anm-worker-small",
  "anm-worker-code",
] as const;
export type ModelName = (typeof MODELS)[number];

// Per-1K-token customer pricing (USD). Provider cost is computed per-adapter.
export const MODEL_PRICING_USD_PER_1K: Record<string, { in: number; out: number }> = {
  "anm-fast-8b": { in: 0.0002, out: 0.0006 },
  "anm-code-7b": { in: 0.0003, out: 0.0009 },
  "anm-pro-70b": { in: 0.0015, out: 0.003 },
  "anm-bittensor-router": { in: 0.0008, out: 0.0016 },
  "anm-embed": { in: 0.0001, out: 0 },
  "anm-worker-small": { in: 0.0002, out: 0.0004 },
  "anm-worker-code": { in: 0.0003, out: 0.0006 },
};

export const CREDIT_PACKAGES = [
  { id: "starter", label: "Starter", amountUsd: 10 },
  { id: "developer", label: "Developer", amountUsd: 50 },
  { id: "startup", label: "Startup", amountUsd: 250 },
  { id: "partner", label: "Partner", amountUsd: 1000 },
] as const;

// ---- Provider-router shared contracts ----
export interface InferenceMessage {
  role: "system" | "user" | "assistant" | "tool";
  content: string;
}

export interface InferenceRequest {
  requestId: string;
  model: string;
  kind: "chat" | "completion" | "embedding";
  messages?: InferenceMessage[];
  prompt?: string;
  input?: string | string[];
  temperature?: number;
  maxTokens?: number;
  stream?: boolean;
}

export interface ProviderHealthResult {
  provider: string;
  healthy: boolean;
  latencyMs?: number;
  error?: string;
}

export interface ProviderCostEstimate {
  provider: string;
  estimatedCostUsd: number;
  estimatedInputTokens: number;
  estimatedOutputTokens: number;
}

export interface ProviderInferenceResult {
  provider: string;
  model: string;
  requestId: string;
  status: "success" | "failed";
  inputTokens: number;
  outputTokens: number;
  providerCostUsd: number;
  latencyMs: number;
  output: string;
  error?: string;
}

export interface ProviderEmbeddingResult {
  provider: string;
  model: string;
  requestId: string;
  status: "success" | "failed";
  inputTokens: number;
  providerCostUsd: number;
  latencyMs: number;
  embeddings: number[][];
  error?: string;
}

export interface ProviderAdapter {
  name: string;
  isEnabled(): Promise<boolean>;
  healthCheck(): Promise<ProviderHealthResult>;
  estimateCost(request: InferenceRequest): Promise<ProviderCostEstimate>;
  runChatCompletion(request: InferenceRequest): Promise<ProviderInferenceResult>;
  runEmbedding?(request: InferenceRequest): Promise<ProviderEmbeddingResult>;
}
