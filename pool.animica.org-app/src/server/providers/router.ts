// Provider router. Given a catalog model, builds the ordered candidate list
// (primary + fallbacks), skips disabled providers, and tries each with a
// timeout until one succeeds — recording health along the way. Returns the
// chosen provider plus its raw response; the route handler turns that into a
// priced, metered, OpenAI-compatible reply.

import { env } from "@/server/env";
import { getModel } from "@/server/ai/catalog";
import { isProviderEnabled, recordResult } from "./health";
import { getProvider } from "./registry";
import {
  ProviderError,
  type ChatProviderResponse,
  type ChatRequest,
  type EmbedProviderResponse,
  type EmbedRequest,
  type ProviderName,
} from "./types";

export class NoProviderError extends Error {
  constructor(model: string) {
    super(`no healthy provider available for model "${model}"`);
    this.name = "NoProviderError";
  }
}

export class UnknownModelError extends Error {
  constructor(model: string) {
    super(`unknown model "${model}"`);
    this.name = "UnknownModelError";
  }
}

function timeout<T>(p: Promise<T>, ms: number, label: string): Promise<T> {
  return Promise.race([
    p,
    new Promise<T>((_, reject) =>
      setTimeout(() => reject(new Error(`${label} timed out after ${ms}ms`)), ms),
    ),
  ]);
}

async function candidates(modelId: string): Promise<ProviderName[]> {
  const model = getModel(modelId);
  if (!model) throw new UnknownModelError(modelId);
  const ordered = [model.provider, ...model.fallbacks];
  const seen = new Set<ProviderName>();
  const out: ProviderName[] = [];
  for (const name of ordered) {
    if (seen.has(name)) continue;
    seen.add(name);
    const provider = getProvider(name);
    if (!provider || !provider.supports(modelId)) continue;
    if (!(await isProviderEnabled(name))) continue;
    out.push(name);
  }
  return out;
}

export interface RouteResult<R> {
  provider: ProviderName;
  result: R;
}

export async function routeChat(
  req: ChatRequest,
): Promise<RouteResult<ChatProviderResponse>> {
  const names = await candidates(req.model);
  if (names.length === 0) throw new NoProviderError(req.model);
  const ms = env().PROVIDER_TIMEOUT_MS;
  let lastErr: unknown;
  for (const name of names) {
    const provider = getProvider(name)!;
    try {
      const result = await timeout(provider.chat(req), ms, `${name}.chat`);
      void recordResult(name, { ok: true, latencyMs: result.latencyMs });
      return { provider: name, result };
    } catch (err) {
      lastErr = err;
      void recordResult(name, { ok: false, latencyMs: ms });
    }
  }
  throw new ProviderError(names[names.length - 1], `all providers failed: ${String(lastErr)}`);
}

export async function routeEmbed(
  req: EmbedRequest,
): Promise<RouteResult<EmbedProviderResponse>> {
  const names = await candidates(req.model);
  if (names.length === 0) throw new NoProviderError(req.model);
  const ms = env().PROVIDER_TIMEOUT_MS;
  let lastErr: unknown;
  for (const name of names) {
    const provider = getProvider(name)!;
    try {
      const result = await timeout(provider.embed(req), ms, `${name}.embed`);
      void recordResult(name, { ok: true, latencyMs: result.latencyMs });
      return { provider: name, result };
    } catch (err) {
      lastErr = err;
      void recordResult(name, { ok: false, latencyMs: ms });
    }
  }
  throw new ProviderError(names[names.length - 1], `all providers failed: ${String(lastErr)}`);
}
