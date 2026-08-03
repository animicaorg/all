/**
 * ANM/USDT price feed — https://animica.org/anm-price.json
 * Refreshed server-side from nonkyc.io by a systemd timer.
 */

import { HttpOptions, getJson } from "./http.js";

export const DEFAULT_PRICE_URL = "https://animica.org/anm-price.json";

export interface AnmPrice {
  /** e.g. "ANM/USDT" */
  symbol: string;
  base: string;
  quote: string;
  /** Last traded price in quote units. */
  last: number;
  bid: number;
  ask: number;
  /** Midpoint of bid/ask. */
  mid: number;
  /** Preferred display price. */
  display: number;
  /** True when the number is indicative rather than from a live trade. */
  is_indicative: boolean;
  /** 24h change, percent. */
  change_percent: number;
  /** 24h volume in ANM. */
  base_volume: number;
  /** 24h volume in USDT. */
  target_volume: number;
  high: number;
  low: number;
  market_url: string;
  pool_url?: string;
  source: string;
  /** Unix timestamp (seconds) of the feed refresh. */
  ts: number;
  [key: string]: unknown;
}

export interface FetchPriceOptions extends HttpOptions {
  url?: string;
}

/** Fetch the current ANM/USDT price document. Throws HttpError on non-2xx. */
export async function fetchPrice(
  opts: FetchPriceOptions = {}
): Promise<AnmPrice> {
  return getJson<AnmPrice>(opts.url ?? DEFAULT_PRICE_URL, opts);
}
