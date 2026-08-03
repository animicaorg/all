/**
 * Chain stats — https://animica.org/api/stats
 *
 * Typed to mirror the real chain-stats service response (snake_case, exactly
 * as emitted by the API). Every field is optional and unknown extra keys are
 * preserved, so newer service fields never break consumers; values the service
 * cannot compute at a given moment are `null`.
 *
 * Note: the public https://animica.org/api/stats URL goes live with the
 * animica.org deploy. Until then (or against your own mirror) pass an
 * override: `fetchStats({ url: "http://127.0.0.1:8560/api/stats" })`.
 */

import { HttpOptions, getJson } from "./http.js";

export const DEFAULT_STATS_URL = "https://animica.org/api/stats";

/** One mining pool entry in `ChainStats.pools`. */
export interface ChainStatsPool {
  /** Public pool name, e.g. "Animica Official Pool". */
  name?: string;
  /** Pool website, e.g. "https://pool.animica.org". */
  url?: string;
  /** Stratum endpoint, e.g. "stratum+tcp://pool.animica.org:3333". */
  stratum?: string;
  /** Solo-mode stratum endpoint (finder keeps the block). */
  stratum_solo?: string;
  /** Pool fee in basis points. */
  fee_bps?: number;
  /** Solo-mode fee in basis points. */
  solo_fee_bps?: number;
  /** Payout scheme, e.g. "pps". */
  payout_scheme?: string;
  /** Connected miners. */
  miners?: number | null;
  /** Connected workers. */
  workers?: number | null;
  /** Total blocks found by the pool. */
  blocks_found?: number | null;
  /** Observed pool hashrate in H/s. */
  pool_hashrate_hs?: number | null;
  [key: string]: unknown;
}

/** Block-reward split in ANM. */
export interface ChainStatsRewardBreakdown {
  /** Miner share of the block subsidy, in ANM. */
  miner?: number;
  /** Foundation share of the block subsidy, in ANM. */
  foundation?: number;
  [key: string]: unknown;
}

/** Supply figures in whole ANM. */
export interface ChainStatsSupply {
  total_anm?: number;
  circulating_anm?: number;
  max_anm?: number;
  [key: string]: unknown;
}

/**
 * The /api/stats document. Field names/types mirror the live service
 * (snake_case). All fields optional; unknown fields are preserved.
 */
export interface ChainStats {
  /** Chain name, "Animica". */
  name?: string;
  /** Coin symbol, "ANM". */
  symbol?: string;
  chain_id?: number;
  /** e.g. "SHA3-256 PoW (PoIES)". */
  algorithm?: string;
  /** Canonical chain height. */
  height?: number;
  /** PoIES acceptance threshold Θ in micro-nats (see `difficulty_unit`). */
  difficulty?: number;
  /** Human description of the difficulty unit. */
  difficulty_unit?: string;
  /** Expected raw SHA3-256 hashes per block at current difficulty. */
  expected_hashes_per_block?: number | null;
  /** Estimated network hashrate in H/s (theta-derived; see source field). */
  network_hashrate_hs?: number | null;
  /** How network_hashrate_hs is computed. */
  network_hashrate_source?: string;
  /** Network hashrate as observed from the pool's share flow, H/s. */
  pool_observed_hashrate_hs?: number | null;
  /** Target block time in seconds. */
  block_time_target_s?: number;
  /** Average block time over the last hour, seconds. */
  avg_block_time_1h_s?: number | null;
  /** Total block subsidy in ANM. */
  block_reward?: number;
  /** Miner/foundation split of the block subsidy, in ANM. */
  block_reward_breakdown?: ChainStatsRewardBreakdown;
  /** ISO-8601 timestamp of the latest block, or null. */
  last_block_time?: string | null;
  /** Last trade price in USD, or null when no feed. */
  price_usd?: number | null;
  /** Last trade price in BTC (currently always null). */
  price_btc?: number | null;
  volume_24h_usd?: number | null;
  volume_24h_anm?: number | null;
  /** Market page, e.g. "https://nonkyc.io/market/ANM_USDT". */
  market_url?: string | null;
  /** Known public mining pools. */
  pools?: ChainStatsPool[];
  /** Supply figures in whole ANM. */
  supply?: ChainStatsSupply;
  /** ISO-8601 timestamp the stats snapshot was computed. */
  updated_at?: string;
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
