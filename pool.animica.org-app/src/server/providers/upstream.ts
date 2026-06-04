// Shared types + helpers for real (networked) provider adapters.

import { add, div, mul } from "@/lib/money";
import { env } from "@/server/env";

// Per catalog-model upstream config: which real model to call, and what it
// costs us per 1k tokens (used for margin accounting). Sell price stays in the
// catalog; this is the COST side.
export interface UpstreamModel {
  model: string; // the upstream model id, e.g. "gpt-4o-mini" or "claude-haiku-4-5-20251001"
  costInPer1kUsd: string;
  costOutPer1kUsd: string;
}

export type ModelMap = Record<string, UpstreamModel>;

export function providerCost(
  m: UpstreamModel,
  inputTokens: number,
  outputTokens: number,
): string {
  return add(
    mul(div(inputTokens, 1000), m.costInPer1kUsd),
    mul(div(outputTokens, 1000), m.costOutPer1kUsd),
  );
}

export function timeoutSignal(): AbortSignal {
  // Abort the underlying fetch (the router's Promise.race can't cancel it).
  return AbortSignal.timeout(env().PROVIDER_TIMEOUT_MS);
}

export async function readErrorBody(res: Response): Promise<string> {
  try {
    return (await res.text()).slice(0, 300);
  } catch {
    return res.statusText;
  }
}
