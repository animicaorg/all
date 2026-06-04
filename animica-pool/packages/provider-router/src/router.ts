import type { ProviderAdapter, InferenceRequest, ProviderInferenceResult } from "@animica/shared";
import { PROVIDER_PRIORITY } from "./config.js";

export interface RouteResult {
  result: ProviderInferenceResult;
  attempts: { provider: string; ok: boolean; error?: string }[];
}

/** Order enabled adapters by priority (Bittensor first … mock last). */
export async function orderedEnabled(adapters: ProviderAdapter[]): Promise<ProviderAdapter[]> {
  const flags = await Promise.all(adapters.map((a) => a.isEnabled().catch(() => false)));
  return adapters
    .filter((_, i) => flags[i])
    .sort((a, b) => (PROVIDER_PRIORITY[a.name] ?? 500) - (PROVIDER_PRIORITY[b.name] ?? 500));
}

/** Run a chat completion across providers with fallback on failure. */
export async function routeChatCompletion(
  req: InferenceRequest,
  adapters: ProviderAdapter[],
  opts: { preferredModelRouting?: boolean } = {},
): Promise<RouteResult> {
  void opts;
  const ordered = await orderedEnabled(adapters);
  if (ordered.length === 0) throw new Error("No inference providers are enabled");

  const attempts: RouteResult["attempts"] = [];
  for (const adapter of ordered) {
    try {
      const result = await adapter.runChatCompletion(req);
      attempts.push({ provider: adapter.name, ok: result.status === "success" });
      if (result.status === "success") return { result, attempts };
    } catch (e) {
      attempts.push({ provider: adapter.name, ok: false, error: String(e) });
    }
  }
  throw new Error(`All providers failed: ${attempts.map((a) => `${a.provider}(${a.error ?? "fail"})`).join(", ")}`);
}

export async function routeEmbedding(req: InferenceRequest, adapters: ProviderAdapter[]) {
  const ordered = await orderedEnabled(adapters);
  for (const adapter of ordered) {
    if (!adapter.runEmbedding) continue;
    try {
      const result = await adapter.runEmbedding(req);
      if (result.status === "success") return result;
    } catch {
      /* try next */
    }
  }
  throw new Error("No provider could serve the embedding request");
}
