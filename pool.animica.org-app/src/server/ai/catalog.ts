// Public model catalog — the menu developers see at GET /v1/models.
//
// Each entry maps a public model id to the provider that serves it plus the
// per-1K-token SELL price (what the customer is charged). Today everything
// routes to the `mock` provider; as real adapters land, only `provider` /
// `fallbacks` change here — routes, prices, and accounting stay put.
//
// Prices are Decimal strings (USD per 1,000 tokens). Embedding models price
// input only (output price unused).

import type { ProviderName } from "@/server/providers/types";

export type ModelKind = "chat" | "embedding";

export interface CatalogModel {
  id: string;
  kind: ModelKind;
  /** Primary provider, then ordered fallbacks. */
  provider: ProviderName;
  fallbacks: ProviderName[];
  priceInPer1kUsd: string;
  priceOutPer1kUsd: string;
  description: string;
}

// `provider` is the PREFERRED route; if it isn't configured (no creds) or fails,
// the router falls through `fallbacks` — `mock` is always the safety net, so the
// API works with zero provider keys and lights up real routing as keys are added.
export const CATALOG: CatalogModel[] = [
  {
    id: "anm-fast-8b",
    kind: "chat",
    provider: "openai-compatible",
    fallbacks: ["mock"],
    priceInPer1kUsd: "0.0002",
    priceOutPer1kUsd: "0.0005",
    description: "Fast general-purpose chat model (OpenAI-compatible upstream).",
  },
  {
    id: "anm-code-7b",
    kind: "chat",
    provider: "openai-compatible",
    fallbacks: ["mock"],
    priceInPer1kUsd: "0.0003",
    priceOutPer1kUsd: "0.0008",
    description: "Code-specialised model (OpenAI-compatible upstream).",
  },
  {
    id: "anm-pro-70b",
    kind: "chat",
    provider: "openai-compatible",
    fallbacks: ["mock"],
    priceInPer1kUsd: "0.0009",
    priceOutPer1kUsd: "0.0027",
    description: "High-quality model for harder tasks (OpenAI-compatible upstream).",
  },
  {
    id: "anm-claude-haiku",
    kind: "chat",
    provider: "anthropic",
    fallbacks: ["mock"],
    priceInPer1kUsd: "0.0012",
    priceOutPer1kUsd: "0.006",
    description: "Anthropic Claude Haiku — fast, cheap Claude.",
  },
  {
    id: "anm-claude-sonnet",
    kind: "chat",
    provider: "anthropic",
    fallbacks: ["mock"],
    priceInPer1kUsd: "0.004",
    priceOutPer1kUsd: "0.02",
    description: "Anthropic Claude Sonnet — high-quality Claude.",
  },
  {
    id: "anm-bittensor-router",
    kind: "chat",
    provider: "mock", // → "bittensor" once that adapter ships
    fallbacks: ["mock"],
    priceInPer1kUsd: "0.0005",
    priceOutPer1kUsd: "0.0015",
    description: "Routes to a Bittensor subnet (mock until the adapter ships).",
  },
  {
    id: "anm-embed",
    kind: "embedding",
    provider: "openai-compatible",
    fallbacks: ["mock"],
    priceInPer1kUsd: "0.00002",
    priceOutPer1kUsd: "0",
    description: "Text embedding model (OpenAI-compatible upstream; 16-dim in mock).",
  },
];

const BY_ID = new Map(CATALOG.map((m) => [m.id, m]));

export function getModel(id: string): CatalogModel | undefined {
  return BY_ID.get(id);
}

export function listModels(): CatalogModel[] {
  return CATALOG;
}
