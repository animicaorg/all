/**
 * NonKYC ANM/USDT price helper — lets token stats render in USD alongside ANM.
 *
 * NonKYC's API sends no CORS headers, so a direct browser fetch usually fails;
 * the explorer host publishes the same quote as a same-origin /anm-price.json
 * feed (see web/public/anm-ticker.js). We try the NonKYC API first (works in
 * dev proxies / if CORS ever opens), then fall back to the same-origin feed.
 * The result is cached for 60 seconds; failures resolve to null (callers show
 * ANM-only figures — never a fabricated USD number).
 */

const NONKYC_URL = 'https://api.nonkyc.io/api/v2/market/getbysymbol/ANM_USDT'
const SAME_ORIGIN_FEED = '/anm-price.json'
const CACHE_TTL_MS = 60_000

let cached: { value: number | null; at: number } | null = null
let inflight: Promise<number | null> | null = null

async function fetchJson(url: string, timeoutMs: number): Promise<Record<string, unknown> | null> {
  try {
    const res = await fetch(url, { signal: AbortSignal.timeout(timeoutMs) })
    if (!res.ok) return null
    const json: unknown = await res.json()
    return json && typeof json === 'object' ? (json as Record<string, unknown>) : null
  } catch {
    return null
  }
}

function toPositiveNumber(value: unknown): number | null {
  const n = typeof value === 'string' ? Number.parseFloat(value) : typeof value === 'number' ? value : NaN
  return Number.isFinite(n) && n > 0 ? n : null
}

async function loadPrice(): Promise<number | null> {
  const fromNonkyc = await fetchJson(NONKYC_URL, 4000)
  if (fromNonkyc) {
    const price = toPositiveNumber(fromNonkyc.lastPriceNumber ?? fromNonkyc.lastPrice)
    if (price !== null) return price
  }
  const fromFeed = await fetchJson(SAME_ORIGIN_FEED, 4000)
  if (fromFeed) {
    const price = toPositiveNumber(fromFeed.last ?? fromFeed.display ?? fromFeed.mid)
    if (price !== null) return price
  }
  return null
}

/** USD per 1 ANM, or null when no quote is reachable. Cached for 60s. */
export async function fetchAnmUsd(): Promise<number | null> {
  const now = Date.now()
  if (cached && now - cached.at < CACHE_TTL_MS) return cached.value
  if (inflight) return inflight
  inflight = loadPrice()
    .then((value) => {
      cached = { value, at: Date.now() }
      return value
    })
    .finally(() => {
      inflight = null
    })
  return inflight
}
