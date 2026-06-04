// Server-side client for the pool's rental engine (/api/rental/*).
//
// Every call is HMAC-signed with POOL_RENTAL_SHARED_SECRET so the pool's
// require_rental_auth accepts it. The signed message is
//   method "\n" path "\n" ts "\n" body
// matching stratum_pool/api.py. Never import this from client components —
// it carries the shared secret.

import { createHmac } from "node:crypto";
import { env } from "./env";

export interface RigSummary {
  rig_id: string;
  worker_name: string;
  hashrate_1m: number;
  hashrate_15m: number;
  hashrate_1h: number;
  last_share_at: number;
  online: boolean;
  pool_mode: string;
  rented: boolean;
}

export interface RentalAssignment {
  id: string;
  owner_worker: string;
  owner_address: string;
  renter_anm_address: string | null;
  renter_xmr_anim_address: string | null;
  anm_mode: string | null;
  coins: string;
  start_ts: number;
  end_ts: number;
  status: string;
  created_ts: number;
}

class PoolApiError extends Error {
  constructor(public status: number, message: string) {
    super(message);
    this.name = "PoolApiError";
  }
}

async function signedFetch<T>(
  method: "GET" | "POST",
  path: string,
  body?: unknown,
): Promise<T> {
  const e = env();
  const ts = Math.floor(Date.now() / 1000).toString();
  const rawBody = method === "POST" && body !== undefined ? JSON.stringify(body) : "";
  const message = `${method}\n${path}\n${ts}\n${rawBody}`;
  const sig = createHmac("sha256", e.POOL_RENTAL_SHARED_SECRET).update(message).digest("hex");
  const res = await fetch(`${e.POOL_API_BASE_URL}${path}`, {
    method,
    headers: {
      "content-type": "application/json",
      "x-rental-sig": sig,
      "x-rental-ts": ts,
    },
    body: method === "POST" ? rawBody : undefined,
    cache: "no-store",
  });
  const text = await res.text();
  let parsed: unknown = undefined;
  try {
    parsed = text ? JSON.parse(text) : undefined;
  } catch {
    /* non-JSON error body */
  }
  if (!res.ok) {
    const detail =
      (parsed as { detail?: string } | undefined)?.detail || text || res.statusText;
    throw new PoolApiError(res.status, `pool ${method} ${path} → ${res.status}: ${detail}`);
  }
  return parsed as T;
}

export const poolClient = {
  isConflict(err: unknown): boolean {
    return err instanceof PoolApiError && err.status === 409;
  },

  async listRigs(): Promise<RigSummary[]> {
    const out = await signedFetch<{ items: RigSummary[] }>("GET", "/api/rental/rigs");
    return out.items ?? [];
  },

  async rigStats(rigId: string): Promise<RigSummary | null> {
    try {
      return await signedFetch<RigSummary>("GET", `/api/rental/rigs/${rigId}/stats`);
    } catch (err) {
      if (err instanceof PoolApiError && err.status === 404) return null;
      throw err;
    }
  },

  async createAssignment(input: {
    rentalId: string;
    ownerWorker: string;
    ownerAddress: string;
    coins: "ANM" | "XMR" | "BOTH";
    startTs: number;
    endTs: number;
    renterAnmAddress?: string | null;
    renterXmrAnimAddress?: string | null;
    anmMode?: "pps" | "solo" | null;
  }): Promise<{ ok: boolean; rig_id: string; assignment: RentalAssignment }> {
    return signedFetch("POST", "/api/rental/assignments", {
      rental_id: input.rentalId,
      owner_worker: input.ownerWorker,
      owner_address: input.ownerAddress,
      coins: input.coins,
      start_ts: input.startTs,
      end_ts: input.endTs,
      renter_anm_address: input.renterAnmAddress ?? null,
      renter_xmr_anim_address: input.renterXmrAnimAddress ?? null,
      anm_mode: input.anmMode ?? null,
    });
  },

  async cancelAssignment(rentalId: string): Promise<{ ok: boolean; cancelled: boolean }> {
    return signedFetch("POST", `/api/rental/assignments/${rentalId}/cancel`);
  },

  async getAssignment(rentalId: string): Promise<{
    assignment: RentalAssignment;
    rig: RigSummary | null;
    window: { measured_uptime_seconds: number; active_buckets: number; window_seconds: number };
  } | null> {
    try {
      return await signedFetch("GET", `/api/rental/assignments/${rentalId}`);
    } catch (err) {
      if (err instanceof PoolApiError && err.status === 404) return null;
      throw err;
    }
  },

  async ownershipChallenge(address: string): Promise<{
    address: string;
    claim_worker: string;
    instructions: string;
    expires_ts: number;
  }> {
    return signedFetch("POST", "/api/rental/ownership/challenge", { address });
  },

  async ownershipVerify(
    address: string,
    claimWorker: string,
  ): Promise<{ ok: boolean; proven: boolean; address: string }> {
    return signedFetch("POST", "/api/rental/ownership/verify", {
      address,
      claim_worker: claimWorker,
    });
  },

  /** Register an anim1→Monero payout mapping so a renter's redirected XMR
   *  credit gets paid to their Monero address. Uses the pool's existing
   *  public /api/pool/xmr/register endpoint (no HMAC). */
  async xmrRegister(anim1Address: string, moneroAddress: string): Promise<void> {
    const e = env();
    const res = await fetch(`${e.POOL_API_BASE_URL}/api/pool/xmr/register`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({
        animica_address: anim1Address,
        payout_currency: "xmr",
        xmr_address: moneroAddress,
      }),
      cache: "no-store",
    });
    if (!res.ok) {
      throw new PoolApiError(res.status, `xmr register → ${res.status}: ${await res.text()}`);
    }
  },
};
