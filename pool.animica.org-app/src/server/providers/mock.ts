// Built-in mock/echo provider.
//
// Lets the entire broker pipeline — auth, metering, atomic credit debit,
// accounting, OpenAI-compatible envelopes — run end-to-end with NO external
// credentials. Output is deterministic (a function of the input), so tests
// are stable. Real providers slot in behind the same `Provider` interface.
//
// NOTE: no Math.random / Date.now-derived randomness — determinism is a
// feature here, and the workflow sandbox forbids those anyway.

import { mul } from "@/lib/money";
import type {
  ChatProviderResponse,
  ChatRequest,
  EmbedProviderResponse,
  EmbedRequest,
  Provider,
} from "./types";

// What the mock "costs" us to serve — a deliberately cheap synthetic supply
// price so there's a realistic positive margin against catalog sell prices.
const MOCK_COST_PER_TOKEN_USD = "0.0000001"; // $0.10 / 1M tokens
const EMBED_DIM = 16;

/** Rough OpenAI-style token estimate (~4 chars/token). Min 1 for non-empty. */
export function estimateTokens(text: string): number {
  if (!text) return 0;
  return Math.max(1, Math.ceil(text.length / 4));
}

// Small, stable string hash (FNV-1a) — used for deterministic latency and
// pseudo-embeddings without any RNG.
function fnv1a(str: string): number {
  let h = 0x811c9dc5;
  for (let i = 0; i < str.length; i++) {
    h ^= str.charCodeAt(i);
    h = Math.imul(h, 0x01000193);
  }
  return h >>> 0;
}

function syntheticLatency(seed: number): number {
  // 40–160ms, deterministic.
  return 40 + (seed % 121);
}

export const mockProvider: Provider = {
  name: "mock",

  supports() {
    // The mock can stand in for any model — it's the universal fallback.
    return true;
  },

  async chat(req: ChatRequest): Promise<ChatProviderResponse> {
    const prompt = req.messages.map((m) => `${m.role}: ${m.content}`).join("\n");
    const inputTokens = estimateTokens(prompt);
    const lastUser =
      [...req.messages].reverse().find((m) => m.role === "user")?.content ?? "";
    const output =
      `[animica:${req.model} via mock] You said: ${lastUser.trim()}`.slice(
        0,
        // honour maxTokens loosely (≈4 chars/token) so usage looks realistic
        req.maxTokens ? req.maxTokens * 4 : 2000,
      );
    const outputTokens = estimateTokens(output);
    const seed = fnv1a(prompt);
    return {
      output,
      inputTokens,
      outputTokens,
      providerCostUsd: mul(inputTokens + outputTokens, MOCK_COST_PER_TOKEN_USD),
      latencyMs: syntheticLatency(seed),
    };
  },

  async embed(req: EmbedRequest): Promise<EmbedProviderResponse> {
    let inputTokens = 0;
    const embeddings = req.input.map((text) => {
      inputTokens += estimateTokens(text);
      const base = fnv1a(text);
      // Deterministic unit-ish vector derived from the hash.
      const vec: number[] = [];
      for (let d = 0; d < EMBED_DIM; d++) {
        const h = fnv1a(`${base}:${d}`);
        vec.push((h / 0xffffffff) * 2 - 1); // [-1, 1)
      }
      return vec;
    });
    return {
      embeddings,
      inputTokens,
      providerCostUsd: mul(inputTokens, MOCK_COST_PER_TOKEN_USD),
      latencyMs: syntheticLatency(fnv1a(req.input.join("|"))),
    };
  },
};
