/**
 * Chain stats — https://animica.org/api/stats
 *
 * Typed loosely on purpose: the chain-stats API is being finalized in
 * parallel with this SDK. Every field is optional and unknown extra keys
 * are preserved; treat any field you rely on defensively.
 */

import { HttpOptions, getJson } from "./http.js";

export const DEFAULT_STATS_URL = "https://animica.org/api/stats";

export interface ChainStats {
  /** Canonical chain height. */
  height?: number;
  /** Latest block hash. */
  blockHash?: string;
  chainId?: number;
  /** PoIES acceptance threshold Θ in micro-nats (the difficulty field). */
  thetaMicro?: number;
  /** Difficulty alias, if the API exposes one. */
  difficulty?: number;
  /** Estimated network hashrate (H/s) — check accompanying unit fields. */
  networkHashrate?: number;
  /** Pool hashrate (H/s), when reported. */
  poolHashrate?: number;
  /** Target/average block time in seconds. */
  blockTime?: number;
  avgBlockTime?: number;
  /** Current block reward in ANM (miner share). */
  blockReward?: number;
  /** Total supply — may be a decimal string or number depending on the API. */
  totalSupply?: string | number;
  circulatingSupply?: string | number;
  maxSupply?: string | number;
  /** Non-zero account count. */
  addressCount?: number;
  /** Last price in USDT, when embedded. */
  priceUsdt?: number;
  /** Unix timestamp (seconds) the stats were computed. */
  ts?: number;
  updatedAt?: string | number;
  [key: string]: unknown;
}

export interface FetchStatsOptions extends HttpOptions {
  url?: string;
}

/** Fetch the public chain stats document. Throws HttpError on non-2xx. */
export async function fetchStats(
  opts: FetchStatsOptions = {}
): Promise<ChainStats> {
  return getJson<ChainStats>(opts.url ?? DEFAULT_STATS_URL, opts);
}
