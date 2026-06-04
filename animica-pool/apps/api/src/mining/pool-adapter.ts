// Adapter over the EXISTING Animica Stratum pool HTTP API (default
// http://127.0.0.1:8550). Falls back to deterministic mock data when
// MINING_USE_MOCK=true or the pool is unreachable — clearly separated from
// the real integration. See README "Reuse existing infrastructure".
import { env } from "../config/env";

const NANO = 1e9; // nanoANM per ANM

export interface NormalizedMiningStats {
  source: "pool" | "mock";
  hashrate: number;
  hashrate24h: number;
  acceptedShares: number;
  rejectedShares: number;
  pendingBalanceAnm: number;
  paidBalanceAnm: number;
  blocksFound: number;
  contributionPct: number;
  poolMode: string | null;
  lastShareAt: number | null;
  xmrOwedAtomic: number;
  xmrPaidAtomic: number;
}

function mockStats(seed: string): NormalizedMiningStats {
  // Deterministic pseudo-stats so the dashboard renders without a live pool.
  let h = 0;
  for (const c of seed) h = (h * 31 + c.charCodeAt(0)) % 100000;
  return {
    source: "mock",
    hashrate: 1000 + (h % 5000),
    hashrate24h: 900 + (h % 4800),
    acceptedShares: h % 5000,
    rejectedShares: h % 40,
    pendingBalanceAnm: (h % 1000) / 100,
    paidBalanceAnm: (h % 5000) / 100,
    blocksFound: h % 3,
    contributionPct: (h % 1000) / 100,
    poolMode: "pps",
    lastShareAt: null,
    xmrOwedAtomic: 0,
    xmrPaidAtomic: 0,
  };
}

async function getJson(url: string, timeoutMs = 4000): Promise<any | null> {
  const ctrl = new AbortController();
  const t = setTimeout(() => ctrl.abort(), timeoutMs);
  try {
    const res = await fetch(url, { signal: ctrl.signal });
    if (!res.ok) return null;
    return await res.json();
  } catch {
    return null;
  } finally {
    clearTimeout(t);
  }
}

export async function getMinerStats(anmAddress: string): Promise<NormalizedMiningStats> {
  const e = env();
  if (e.MINING_USE_MOCK || !e.ANM_POOL_API_URL) return mockStats(anmAddress || "anon");

  const anmApi = e.ANM_POOL_API_URL.replace(/\/$/, "");
  const [miner, xmr] = await Promise.all([
    getJson(`${anmApi}/api/miners/${encodeURIComponent(anmAddress)}`),
    getJson(`${(e.XMR_POOL_API_URL || e.ANM_POOL_API_URL).replace(/\/$/, "")}/api/pool/xmr/miner/${encodeURIComponent(anmAddress)}`),
  ]);
  if (!miner) {
    // Pool reachable but this address has no record yet → real zero stats
    // (NOT mock, which would show fake numbers in production).
    return {
      source: "pool", hashrate: 0, hashrate24h: 0, acceptedShares: 0, rejectedShares: 0,
      pendingBalanceAnm: 0, paidBalanceAnm: 0, blocksFound: 0, contributionPct: 0,
      poolMode: null, lastShareAt: null,
      xmrOwedAtomic: Number(xmr?.owed_atomic ?? 0), xmrPaidAtomic: Number(xmr?.paid_atomic ?? 0),
    };
  }

  const total = Number(miner.total_credit ?? 0);
  const paid = Number(miner.paid_out ?? 0);
  return {
    source: "pool",
    hashrate: Number(miner.hashrate_1m ?? 0),
    hashrate24h: Number(miner.hashrate_1h ?? 0),
    acceptedShares: Number(miner.shares_accepted ?? 0),
    rejectedShares: Number(miner.shares_rejected ?? 0),
    pendingBalanceAnm: Math.max(0, total - paid) / NANO,
    paidBalanceAnm: paid / NANO,
    blocksFound: Number(miner.blocks_found ?? 0),
    contributionPct: Number(miner.contribution_pct ?? 0),
    poolMode: miner.pool_mode ?? null,
    lastShareAt: miner.last_share_at ?? null,
    xmrOwedAtomic: Number(xmr?.owed_atomic ?? 0),
    xmrPaidAtomic: Number(xmr?.paid_atomic ?? 0),
  };
}

export async function getPoolSummary(): Promise<any | null> {
  const e = env();
  if (e.MINING_USE_MOCK || !e.ANM_POOL_API_URL) {
    return { source: "mock", pool_hashrate: 250000, num_miners: 42, blocks_found_total: 137 };
  }
  return getJson(`${e.ANM_POOL_API_URL.replace(/\/$/, "")}/api/pool/summary`);
}

// Per-mode miner connection string (uses the existing CLI + stratum URLs).
export function connectionString(mode: string, anmAddress: string, workerName: string): { poolUrl: string; command: string } {
  const e = env();
  const worker = workerName || "rig-01";
  if (mode === "XMR_ONLY") {
    return {
      poolUrl: e.XMR_POOL_STRATUM_URL,
      command: `animica miner dual-mine ${anmAddress} --only xmr --pool-host pool.animica.org --worker ${worker}`,
    };
  }
  if (mode === "DUAL_50_50") {
    return {
      poolUrl: e.DUAL_POOL_STRATUM_URL,
      command: `animica miner dual-mine ${anmAddress} --pool-host pool.animica.org --threads 8 --worker ${worker}`,
    };
  }
  return {
    poolUrl: e.ANM_POOL_STRATUM_URL,
    command: `animica miner mine-blocks ${anmAddress} --pool-stratum ${e.ANM_POOL_STRATUM_URL} --pool-worker ${worker}`,
  };
}
