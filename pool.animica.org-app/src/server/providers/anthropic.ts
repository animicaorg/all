// Anthropic (Claude) provider adapter (factory).
//
// Claude's Messages API differs from OpenAI: system prompts go in a top-level
// `system` field, `max_tokens` is required, and the response is a list of
// content blocks. Embeddings aren't offered, so embed() throws and the router
// falls back. Configure in providers/config.ts (activates when ANTHROPIC_API_KEY
// is set).

import { estimateTokens } from "./mock";
import { ProviderError, type Provider } from "./types";
import {
  providerCost,
  readErrorBody,
  timeoutSignal,
  type ModelMap,
} from "./upstream";

const ANTHROPIC_VERSION = "2023-06-01";
const DEFAULT_MAX_TOKENS = 1024;

export interface AnthropicConfig {
  apiKey: string;
  baseUrl?: string; // default https://api.anthropic.com
  modelMap: ModelMap;
}

export function makeAnthropicProvider(cfg: AnthropicConfig): Provider {
  const baseUrl = (cfg.baseUrl ?? "https://api.anthropic.com").replace(/\/$/, "");
  const headers = {
    "content-type": "application/json",
    "x-api-key": cfg.apiKey,
    "anthropic-version": ANTHROPIC_VERSION,
  };

  return {
    name: "anthropic",

    supports(model: string) {
      return model in cfg.modelMap;
    },

    async chat(req) {
      const entry = cfg.modelMap[req.model];
      if (!entry) throw new ProviderError("anthropic", `model "${req.model}" not mapped`);

      // System messages → top-level `system`; the rest → user/assistant turns.
      const system = req.messages
        .filter((m) => m.role === "system")
        .map((m) => m.content)
        .join("\n\n");
      const messages = req.messages
        .filter((m) => m.role !== "system")
        .map((m) => ({
          role: m.role === "assistant" ? "assistant" : "user",
          content: m.content,
        }));

      const t0 = Date.now();
      const res = await fetch(`${baseUrl}/v1/messages`, {
        method: "POST",
        headers,
        signal: timeoutSignal(),
        body: JSON.stringify({
          model: entry.model,
          max_tokens: req.maxTokens ?? DEFAULT_MAX_TOKENS,
          temperature: req.temperature,
          system: system || undefined,
          messages,
        }),
      });
      const latencyMs = Date.now() - t0;
      if (!res.ok) {
        throw new ProviderError("anthropic", `${res.status}: ${await readErrorBody(res)}`);
      }
      const data = (await res.json()) as {
        content?: { type: string; text?: string }[];
        usage?: { input_tokens?: number; output_tokens?: number };
      };
      const output = (data.content ?? [])
        .filter((b) => b.type === "text")
        .map((b) => b.text ?? "")
        .join("");
      const promptText = req.messages.map((m) => m.content).join("\n");
      const inputTokens = data.usage?.input_tokens ?? estimateTokens(promptText);
      const outputTokens = data.usage?.output_tokens ?? estimateTokens(output);
      return {
        output,
        inputTokens,
        outputTokens,
        providerCostUsd: providerCost(entry, inputTokens, outputTokens),
        latencyMs,
      };
    },

    async embed() {
      // Anthropic has no embeddings API — fall back to another provider.
      throw new ProviderError("anthropic", "embeddings are not supported by Anthropic");
    },
  };
}
