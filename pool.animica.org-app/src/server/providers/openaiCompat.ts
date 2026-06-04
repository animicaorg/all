// Generic OpenAI-compatible upstream adapter (factory).
//
// One implementation serves every OpenAI-shaped endpoint: OpenAI itself,
// RunPod serverless vLLM (.../openai/v1), Together, OpenRouter, Hyperbolic,
// io.net, or a self-hosted vLLM. Configure instances in providers/config.ts.
//
// The provider only serves catalog models present in its `modelMap`; anything
// else throws so the router falls back (ultimately to the mock).

import { estimateTokens } from "./mock";
import { ProviderError, type Provider, type ProviderName } from "./types";
import {
  providerCost,
  readErrorBody,
  timeoutSignal,
  type ModelMap,
} from "./upstream";

export interface OpenAiCompatConfig {
  name: ProviderName;
  baseUrl: string; // no trailing slash, e.g. https://api.openai.com/v1
  apiKey: string;
  modelMap: ModelMap;
}

export function makeOpenAiCompatProvider(cfg: OpenAiCompatConfig): Provider {
  const headers = {
    "content-type": "application/json",
    authorization: `Bearer ${cfg.apiKey}`,
  };

  return {
    name: cfg.name,

    supports(model: string) {
      return model in cfg.modelMap;
    },

    async chat(req) {
      const entry = cfg.modelMap[req.model];
      if (!entry) throw new ProviderError(cfg.name, `model "${req.model}" not mapped`);
      const t0 = Date.now();
      const res = await fetch(`${cfg.baseUrl}/chat/completions`, {
        method: "POST",
        headers,
        signal: timeoutSignal(),
        body: JSON.stringify({
          model: entry.model,
          messages: req.messages,
          max_tokens: req.maxTokens,
          temperature: req.temperature,
        }),
      });
      const latencyMs = Date.now() - t0;
      if (!res.ok) {
        throw new ProviderError(cfg.name, `${res.status}: ${await readErrorBody(res)}`);
      }
      const data = (await res.json()) as {
        choices?: { message?: { content?: string } }[];
        usage?: { prompt_tokens?: number; completion_tokens?: number };
      };
      const output = data.choices?.[0]?.message?.content ?? "";
      const promptText = req.messages.map((m) => m.content).join("\n");
      const inputTokens = data.usage?.prompt_tokens ?? estimateTokens(promptText);
      const outputTokens = data.usage?.completion_tokens ?? estimateTokens(output);
      return {
        output,
        inputTokens,
        outputTokens,
        providerCostUsd: providerCost(entry, inputTokens, outputTokens),
        latencyMs,
      };
    },

    async embed(req) {
      const entry = cfg.modelMap[req.model];
      if (!entry) throw new ProviderError(cfg.name, `model "${req.model}" not mapped`);
      const t0 = Date.now();
      const res = await fetch(`${cfg.baseUrl}/embeddings`, {
        method: "POST",
        headers,
        signal: timeoutSignal(),
        body: JSON.stringify({
          model: entry.model,
          input: req.input,
          encoding_format: "float", // get plain number[] back, not base64
        }),
      });
      const latencyMs = Date.now() - t0;
      if (!res.ok) {
        throw new ProviderError(cfg.name, `${res.status}: ${await readErrorBody(res)}`);
      }
      const data = (await res.json()) as {
        data?: { index?: number; embedding: number[] }[];
        usage?: { prompt_tokens?: number };
      };
      const rows = (data.data ?? []).slice().sort((a, b) => (a.index ?? 0) - (b.index ?? 0));
      const embeddings = rows.map((r) => r.embedding);
      const inputTokens =
        data.usage?.prompt_tokens ?? req.input.reduce((n, t) => n + estimateTokens(t), 0);
      return {
        embeddings,
        inputTokens,
        providerCostUsd: providerCost(entry, inputTokens, 0),
        latencyMs,
      };
    },
  };
}
