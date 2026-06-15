// Server-side client for the ENA training-pool coordinator HTTP API.
//
// The coordinator URL (ENA_COORDINATOR_BASE_URL) is server-only; the browser
// never sees it. Our /api/pools/* proxy routes call into this module so the
// coordinator stays behind pool.animica.org. Every call fetch()es the
// coordinator and throws CoordinatorError on a non-2xx response.

import { env } from "./env";

/** One pool as returned by GET /pool/list. The coordinator may add fields; we
 *  keep this loose and only type the ones the UI relies on. */
export interface PoolSummary {
  pool_id: string;
  name?: string;
  status?: string;
  base_model?: string;
  method?: string;
  round?: number;
  num_shards?: number;
  funded?: boolean;
  budget_anm?: string | number;
  budget_nano?: string | number;
  [key: string]: unknown;
}

export interface PoolListResponse {
  pools: PoolSummary[];
}

export interface PoolStatus {
  pool_id: string;
  name?: string;
  status?: string;
  base_model?: string;
  method?: string;
  round?: number;
  num_shards?: number;
  shards?: Record<string, number>;
  contributions?: Record<string, number>;
  shards_submitted?: number;
  funded?: boolean;
  budget_nano?: string | number;
  budget_anm?: string | number;
  paid_out_nano?: string | number;
  served_checkpoint?: {
    round?: number;
    checkpoint_hash?: string;
    [key: string]: unknown;
  } | null;
  reward_split?: Record<string, number>;
  [key: string]: unknown;
}

export interface LeaderboardEntry {
  role: string;
  who: string;
  weight: number;
}

export interface LeaderboardResponse {
  leaderboard: LeaderboardEntry[];
}

export interface FundQuote {
  pool_id: string;
  pool_hash: string;
  treasury_address: string;
  chain_id: number | string;
  required_nano: string | number;
  required_anm: string | number;
  memo: string;
  round: number;
  [key: string]: unknown;
}

export interface FundConfirm {
  pool_id: string;
  funded: boolean;
  [key: string]: unknown;
}

export class CoordinatorError extends Error {
  constructor(public status: number, message: string) {
    super(message);
    this.name = "CoordinatorError";
  }
}

async function coordinatorFetch<T>(
  path: string,
  init?: { method?: "GET" | "POST"; body?: unknown },
): Promise<T> {
  const base = env().ENA_COORDINATOR_BASE_URL.replace(/\/+$/, "");
  const method = init?.method ?? "GET";
  const res = await fetch(`${base}${path}`, {
    method,
    headers: { "content-type": "application/json" },
    body:
      method === "POST" && init?.body !== undefined
        ? JSON.stringify(init.body)
        : undefined,
    cache: "no-store",
  });
  const text = await res.text();
  let parsed: unknown = undefined;
  try {
    parsed = text ? JSON.parse(text) : undefined;
  } catch {
    /* non-JSON body — surface the raw text in the error */
  }
  if (!res.ok) {
    const detail =
      (parsed as { error?: string; reason?: string; detail?: string } | undefined)
        ?.error ||
      (parsed as { reason?: string } | undefined)?.reason ||
      (parsed as { detail?: string } | undefined)?.detail ||
      text ||
      res.statusText;
    throw new CoordinatorError(
      res.status,
      `coordinator ${method} ${path} → ${res.status}: ${detail}`,
    );
  }
  return parsed as T;
}

export const coordinatorClient = {
  /** GET /pool/list?status= */
  async listPools(status?: string): Promise<PoolListResponse> {
    const qs = status ? `?status=${encodeURIComponent(status)}` : "";
    return coordinatorFetch<PoolListResponse>(`/pool/list${qs}`);
  },

  /** GET /pool/status?pool_id=ID */
  async poolStatus(poolId: string): Promise<PoolStatus> {
    return coordinatorFetch<PoolStatus>(
      `/pool/status?pool_id=${encodeURIComponent(poolId)}`,
    );
  },

  /** GET /pool/leaderboard?pool_id=ID */
  async poolLeaderboard(poolId: string): Promise<LeaderboardResponse> {
    return coordinatorFetch<LeaderboardResponse>(
      `/pool/leaderboard?pool_id=${encodeURIComponent(poolId)}`,
    );
  },

  /** POST /pool/fund/quote */
  async fundQuote(
    poolId: string,
    rewardAnm: number,
    requester: string,
  ): Promise<FundQuote> {
    return coordinatorFetch<FundQuote>(`/pool/fund/quote`, {
      method: "POST",
      body: { pool_id: poolId, reward_anm: rewardAnm, requester },
    });
  },

  /** POST /pool/fund/confirm */
  async fundConfirm(
    poolId: string,
    txid: string,
    requester: string,
  ): Promise<FundConfirm> {
    return coordinatorFetch<FundConfirm>(`/pool/fund/confirm`, {
      method: "POST",
      body: { pool_id: poolId, txid, requester },
    });
  },
};
