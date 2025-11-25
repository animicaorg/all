/**
 * Faucet service helper (optional).
 * Talks to studio-services POST /faucet/drip to request test funds.
 *
 * Configuration:
 *   - Base URL: import.meta.env.VITE_SERVICES_URL (e.g., "http://localhost:8787")
 *   - API key: pass per-call, or set window.localStorage["studio.services.apiKey"]
 *
 * Usage:
 *   import { drip } from './faucet';
 *   await drip({ address: 'anim1...', amount: '1000000' });
 */

export type Hex = `0x${string}`;

export type DripRequest = {
  /** Bech32m address (anim1...) or hex (0x...) accepted by the service. */
  address: string;
  /** Optional amount as decimal string of smallest unit; service may apply a cap. */
  amount?: string | number;
  /** Optional per-call API key; falls back to localStorage if omitted. */
  apiKey?: string;
  /** Optional override base URL; defaults to VITE_SERVICES_URL. */
  baseUrl?: string;
  /** Request timeout in ms (default 15000). */
  timeoutMs?: number;
};

export type DripResponse = {
  ok: true;
  /** Hash of the faucet transfer transaction. */
  txHash: Hex;
  /** Amount actually dripped (decimal string). */
  amount: string;
  /** Optional new balance after drip, if service provides it. */
  balance?: string;
};

export type DripError = {
  ok: false;
  status: number;
  code?: string;
  message: string;
  retryAfterMs?: number;
};

const DEFAULT_TIMEOUT_MS = 15_000;

function getBaseUrl(override?: string): string {
  const fromEnv = (import.meta as any).env?.VITE_SERVICES_URL as string | undefined;
  const base = override ?? fromEnv;
  if (!base) {
    throw new Error(
      'Faucet services URL not configured. Set VITE_SERVICES_URL or pass baseUrl to drip().'
    );
  }
  return base.replace(/\/+$/, '');
}

function getApiKey(override?: string): string | undefined {
  if (override) return override;
  try {
    return window.localStorage.getItem('studio.services.apiKey') || undefined;
  } catch {
    return undefined;
  }
}

function toDecimalString(v: string | number | undefined): string | undefined {
  if (v === undefined) return undefined;
  if (typeof v === 'number') {
    if (!Number.isFinite(v) || v < 0) throw new Error('amount must be a non-negative finite number');
    return Math.trunc(v).toString(10);
  }
  if (!/^\d+$/.test(v)) throw new Error('amount must be a decimal string of an integer >= 0');
  return v;
}

/** POST JSON with timeout and helpful error shaping. */
async function postJson<T>(
  url: string,
  body: unknown,
  apiKey?: string,
  timeoutMs = DEFAULT_TIMEOUT_MS
): Promise<T> {
  const ctrl = new AbortController();
  const t = setTimeout(() => ctrl.abort(), timeoutMs);
  try {
    const res = await fetch(url, {
      method: 'POST',
      headers: {
        'content-type': 'application/json',
        ...(apiKey ? { authorization: `Bearer ${apiKey}` } : {}),
      },
      body: JSON.stringify(body),
      signal: ctrl.signal,
      credentials: 'omit',
      cache: 'no-store',
    });

    // Success path
    if (res.ok) {
      return (await res.json()) as T;
    }

    // Error shaping
    const retryAfter = parseRetryAfter(res.headers.get('retry-after'));
    let detail: any = null;
    try {
      detail = await res.json();
    } catch {
      // not JSON; ignore
    }

    const err: DripError = {
      ok: false,
      status: res.status,
      code: detail?.code || detail?.error || undefined,
      message:
        detail?.message ||
        detail?.detail ||
        (res.status === 401
          ? 'Unauthorized: missing or invalid API key'
          : res.status === 429
          ? 'Rate limited by faucet'
          : `HTTP ${res.status} while calling faucet`),
      retryAfterMs: retryAfter ?? undefined,
    };
    throw err;
  } catch (e: any) {
    if (e?.ok === false && typeof e.status === 'number') {
      // already a DripError
      throw e;
    }
    if (e?.name === 'AbortError') {
      const err: DripError = { ok: false, status: 0, message: 'Request timed out' };
      throw err;
    }
    const err: DripError = {
      ok: false,
      status: 0,
      message: e?.message || 'Network error while calling faucet',
    };
    throw err;
  } finally {
    clearTimeout(t);
  }
}

function parseRetryAfter(h: string | null): number | null {
  if (!h) return null;
  // Can be seconds or HTTP-date; we only support seconds here.
  const s = Number(h);
  if (Number.isFinite(s) && s >= 0) return Math.round(s * 1000);
  return null;
}

/**
 * Request a faucet drip.
 * Returns { ok: true, txHash, amount, balance? } or throws DripError on failure.
 */
export async function drip(req: DripRequest): Promise<DripResponse> {
  const base = getBaseUrl(req.baseUrl);
  const apiKey = getApiKey(req.apiKey);
  const amount = toDecimalString(req.amount);

  const payload: Record<string, unknown> = { address: req.address };
  if (amount !== undefined) payload.amount = amount;

  const url = `${base}/faucet/drip`;
  const res = await postJson<any>(url, payload, apiKey, req.timeoutMs);

  // Normalize fields defensively
  const txHash = (res.txHash || res.tx_hash) as string;
  const out: DripResponse = {
    ok: true,
    txHash: txHash as Hex,
    amount: String(res.amount ?? amount ?? '0'),
    balance: res.balance !== undefined ? String(res.balance) : undefined,
  };
  return out;
}

/** Convenience: store API key for later calls (localStorage). */
export function setApiKey(key: string | null): void {
  try {
    if (key) window.localStorage.setItem('studio.services.apiKey', key);
    else window.localStorage.removeItem('studio.services.apiKey');
  } catch {
    // ignore storage failures
  }
}

/** Retrieve stored API key (if any). */
export function getStoredApiKey(): string | undefined {
  return getApiKey(undefined);
}

export default { drip, setApiKey, getStoredApiKey };
