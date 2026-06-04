// Provider health tracking. The router consults `isEnabled` before routing
// and records latency / success after each call. All writes are best-effort:
// health bookkeeping must never fail an inference request.

import { prisma } from "@/server/db";
import type { ProviderName } from "./types";

export async function isProviderEnabled(name: ProviderName): Promise<boolean> {
  try {
    const row = await prisma.providerHealth.findUnique({ where: { provider: name } });
    // Unknown providers default to enabled — health is opt-out, not opt-in.
    return row ? row.isEnabled : true;
  } catch {
    return true;
  }
}

export async function recordResult(
  name: ProviderName,
  outcome: { ok: boolean; latencyMs: number },
): Promise<void> {
  try {
    const existing = await prisma.providerHealth.findUnique({ where: { provider: name } });
    // Exponential moving averages so a single blip doesn't dominate.
    const prevLatency = existing?.avgLatencyMs ?? outcome.latencyMs;
    const avgLatencyMs = Math.round(prevLatency * 0.8 + outcome.latencyMs * 0.2);
    const prevRate = existing ? Number(existing.successRate) : 100;
    const successRate = (prevRate * 0.9 + (outcome.ok ? 100 : 0) * 0.1).toFixed(2);
    await prisma.providerHealth.upsert({
      where: { provider: name },
      update: { avgLatencyMs, successRate },
      create: { provider: name, avgLatencyMs, successRate },
    });
  } catch (err) {
    // eslint-disable-next-line no-console
    console.error("[providerHealth] record failed:", err);
  }
}
