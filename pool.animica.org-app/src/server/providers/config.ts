// Real-provider configuration. Each provider activates ONLY when its
// credentials are present in the environment; otherwise the registry contains
// just the mock and every catalog model falls back to it. So with no keys the
// broker behaves exactly as in the foundation phase, and adding a key lights up
// real routing with zero code changes.
//
// Model maps (catalog model id → upstream model + per-1k cost) have sensible
// defaults for providers with stable public model names (Anthropic, OpenAI),
// and can be overridden with a JSON env var. RunPod serves an endpoint-specific
// model, so it has no default map — set RUNPOD_MODEL_MAP to use it.

import { makeAnthropicProvider } from "./anthropic";
import { makeOpenAiCompatProvider } from "./openaiCompat";
import type { Provider } from "./types";
import type { ModelMap } from "./upstream";

function parseModelMap(raw: string | undefined): ModelMap | null {
  if (!raw) return null;
  try {
    return JSON.parse(raw) as ModelMap;
  } catch {
    // eslint-disable-next-line no-console
    console.error("[providers] invalid model map JSON, ignoring:", raw.slice(0, 80));
    return null;
  }
}

// Claude models — ids per the current Claude 4.x family.
const DEFAULT_ANTHROPIC_MAP: ModelMap = {
  "anm-claude-haiku": {
    model: "claude-haiku-4-5-20251001",
    costInPer1kUsd: "0.0008",
    costOutPer1kUsd: "0.004",
  },
  "anm-claude-sonnet": {
    model: "claude-sonnet-4-6",
    costInPer1kUsd: "0.003",
    costOutPer1kUsd: "0.015",
  },
};

// When OPENAI_UPSTREAM_BASE_URL points at OpenAI proper, these defaults work.
// For a vLLM/RunPod/OpenRouter upstream, override with OPENAI_UPSTREAM_MODEL_MAP.
const DEFAULT_OPENAI_MAP: ModelMap = {
  "anm-fast-8b": { model: "gpt-4o-mini", costInPer1kUsd: "0.00015", costOutPer1kUsd: "0.0006" },
  "anm-code-7b": { model: "gpt-4o-mini", costInPer1kUsd: "0.00015", costOutPer1kUsd: "0.0006" },
  "anm-pro-70b": { model: "gpt-4o", costInPer1kUsd: "0.0025", costOutPer1kUsd: "0.01" },
  "anm-embed": { model: "text-embedding-3-small", costInPer1kUsd: "0.00002", costOutPer1kUsd: "0" },
};

export function buildRealProviders(): Provider[] {
  const e = process.env;
  const providers: Provider[] = [];

  // Anthropic / Claude
  if (e.ANTHROPIC_API_KEY) {
    providers.push(
      makeAnthropicProvider({
        apiKey: e.ANTHROPIC_API_KEY,
        baseUrl: e.ANTHROPIC_BASE_URL,
        modelMap: parseModelMap(e.ANTHROPIC_MODEL_MAP) ?? DEFAULT_ANTHROPIC_MAP,
      }),
    );
  }

  // OpenAI / any OpenAI-compatible upstream
  if (e.OPENAI_UPSTREAM_API_KEY && e.OPENAI_UPSTREAM_BASE_URL) {
    providers.push(
      makeOpenAiCompatProvider({
        name: "openai-compatible",
        baseUrl: e.OPENAI_UPSTREAM_BASE_URL.replace(/\/$/, ""),
        apiKey: e.OPENAI_UPSTREAM_API_KEY,
        modelMap: parseModelMap(e.OPENAI_UPSTREAM_MODEL_MAP) ?? DEFAULT_OPENAI_MAP,
      }),
    );
  }

  // RunPod serverless vLLM — uses its OpenAI-compatible route. Endpoint-specific
  // model, so a map is required (no default).
  if (e.RUNPOD_API_KEY && e.RUNPOD_ENDPOINT_ID) {
    const map = parseModelMap(e.RUNPOD_MODEL_MAP);
    if (map) {
      providers.push(
        makeOpenAiCompatProvider({
          name: "runpod",
          baseUrl: `https://api.runpod.ai/v2/${e.RUNPOD_ENDPOINT_ID}/openai/v1`,
          apiKey: e.RUNPOD_API_KEY,
          modelMap: map,
        }),
      );
    }
  }

  return providers;
}
