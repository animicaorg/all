/**
 * RPC / WS Health Probes
 * -----------------------------------------------------------------------------
 * Lightweight utilities to measure:
 *  - RPC JSON-RPC round-trip latency (and basic availability)
 *  - WebSocket connect time and optional JSON-RPC ping response time
 *
 * These functions are isomorphic (Browser + Node). For Node WS, we lazily
 * import the 'ws' package if global WebSocket is not present.
 */

type Json = null | boolean | number | string | Json[] | { [k: string]: Json };

export interface RpcHealthOptions {
  rpcUrl: string;                          // e.g. https://rpc.devnet.animica.xyz
  headers?: Record<string, string>;
  timeoutMs?: number;                      // default 6000
  /** Optional explicit probe method; otherwise a fallback chain is used. */
  method?: string;
  /** Optional params for the explicit method. */
  params?: any;
}

export interface RpcHealthSample {
  ok: boolean;
  url: string;
  latencyMs?: number;                      // round-trip time
  methodUsed?: string;
  httpStatus?: number;
  resultPreview?: string;                  // short preview of result for debugging
  error?: string;                          // message when !ok
  timestamp: string;                       // ISO
}

export interface WsHealthOptions {
  wsUrl?: string;                          // wss://... (or derived from rpcUrl)
  rpcUrlForDerive?: string;                // used when wsUrl not provided
  timeoutMs?: number;                      // default 6000
  /**
   * Optional JSON-RPC ping to send after connect.
   * If omitted, only connect time is measured.
   */
  pingMethod?: string;                     // default 'omni_ping' with {} params
  pingParams?: any;
  /** Optional headers for Node 'ws' only (browser ignores). */
  headers?: Record<string, string>;
}

export interface WsHealthSample {
  ok: boolean;
  url: string;
  connectMs?: number;
  pingMs?: number;                         // time to complete optional JSON-RPC ping
  error?: string;
  timestamp: string;                       // ISO
}

/* -------------------------------- Utilities -------------------------------- */

function nowMs(): number {
  // Prefer high-resolution timer when available
  return (typeof performance !== 'undefined' && performance.now)
    ? performance.now()
    : Date.now();
}

function sleep(ms: number): Promise<void> {
  return new Promise((r) => setTimeout(r, ms));
}

function abbrevResult(v: unknown): string {
  try {
    const s = JSON.stringify(v);
    return s.length > 120 ? s.slice(0, 117) + '...' : s;
  } catch {
    return String(v);
  }
}

function normalizeUrlNoTrailingSlash(u: string): string {
  return u.replace(/\/+$/, '');
}

function errToString(e: any): string {
  if (!e) return 'Unknown error';
  if (typeof e === 'string') return e;
  if (e.name === 'AbortError') return 'Timeout';
  if (e?.message) return e.message;
  try {
    return JSON.stringify(e);
  } catch {
    return String(e);
  }
}

function isRpcError(obj: any): boolean {
  return !!(obj && typeof obj === 'object' && 'error' in obj);
}

/**
 * Derive ws(s) URL from http(s) URL
 *  https://host/x  -> wss://host/x/ws or wss://host/x (heuristics)
 * We conservatively just swap protocol and keep path.
 */
export function deriveWsUrlFromRpc(rpcUrl: string): string {
  const u = new URL(rpcUrl);
  u.protocol = u.protocol === 'https:' ? 'wss:' : 'ws:';
  return u.toString();
}

/* ---------------------------------- RPC ping -------------------------------- */

const DEFAULT_RPC_FALLBACK_METHODS = [
  'omni_ping',            // preferred lightweight method
  'omni_getHead',         // head info
  'web3_clientVersion',   // common in many nodes
  'net_version',          // network id/version
  'rpc.discover',         // OpenRPC schema
];

export async function rpcHealth(opts: RpcHealthOptions): Promise<RpcHealthSample> {
  const url = normalizeUrlNoTrailingSlash(opts.rpcUrl);
  const timeoutMs = opts.timeoutMs ?? 6000;
  const headers = {
    'Content-Type': 'application/json',
    Accept: 'application/json',
    ...(opts.headers ?? {}),
  };

  const methods = opts.method ? [opts.method] : DEFAULT_RPC_FALLBACK_METHODS;
  let lastErr: any;
  let status: number | undefined;

  for (const method of methods) {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), timeoutMs);

    const id = Math.floor(Math.random() * 1e9);
    const body = JSON.stringify({
      jsonrpc: '2.0',
      id,
      method,
      params: opts.params ?? (method === 'omni_ping' ? {} : undefined),
    });

    const t0 = nowMs();
    try {
      const res = await fetch(url, { method: 'POST', headers, body, signal: controller.signal });
      status = res.status;
      if (!res.ok) {
        lastErr = new Error(`HTTP ${res.status} ${res.statusText}`);
        clearTimeout(timer);
        // If 404/405/etc., try next fallback method.
        continue;
      }
      const json = await res.json();
      clearTimeout(timer);

      if (isRpcError(json)) {
        // -32601 (method not found) → try next fallback
        const code = json?.error?.code;
        if (code === -32601) {
          lastErr = new Error(`RPC method ${method} not found`);
          continue;
        }
        // Other RPC error: treat as failure for this method
        lastErr = new Error(`RPC error (${code}): ${json?.error?.message ?? 'unknown'}`);
        continue;
      }

      const dt = Math.max(0, nowMs() - t0);
      return {
        ok: true,
        url,
        latencyMs: dt,
        methodUsed: method,
        httpStatus: status,
        resultPreview: abbrevResult(json?.result),
        timestamp: new Date().toISOString(),
      };
    } catch (e) {
      clearTimeout(timer);
      lastErr = e;
      // Try next fallback
    }
  }

  return {
    ok: false,
    url,
    error: errToString(lastErr),
    httpStatus: status,
    timestamp: new Date().toISOString(),
  };
}

/* ---------------------------------- WS ping --------------------------------- */

/**
 * Obtain a WebSocket constructor (Browser or Node).
 */
async function getWebSocketCtor(): Promise<typeof WebSocket> {
  if (typeof WebSocket !== 'undefined') return WebSocket;
  // Node: try to lazy import 'ws'
  const ws = await import('ws').catch(() => null as any);
  if (!ws) throw new Error('WebSocket not available; install the "ws" package in Node environments');
  return (ws.WebSocket || ws.default || ws) as unknown as typeof WebSocket;
}

export async function wsHealth(opts: WsHealthOptions): Promise<WsHealthSample> {
  const timeoutMs = opts.timeoutMs ?? 6000;
  const url = opts.wsUrl
    ? opts.wsUrl
    : (opts.rpcUrlForDerive ? deriveWsUrlFromRpc(opts.rpcUrlForDerive) : '');

  if (!url) {
    return {
      ok: false,
      url,
      error: 'wsUrl not provided and could not derive from rpcUrl',
      timestamp: new Date().toISOString(),
    };
  }

  let connectMs: number | undefined;
  let pingMs: number | undefined;

  try {
    const WS = await getWebSocketCtor();
    const t0 = nowMs();

    // Node 'ws' accepts headers; browser ignores this extra arg.
    const ws = new WS(url, undefined as any, (opts.headers ? { headers: opts.headers } : undefined) as any);

    // Await 'open' or timeout
    await new Promise<void>((resolve, reject) => {
      const to = setTimeout(() => {
        try { ws.close(); } catch {}
        reject(new Error('WS connect timeout'));
      }, timeoutMs);

      ws.addEventListener('open', () => {
        clearTimeout(to);
        resolve();
      });
      ws.addEventListener('error', (ev: any) => {
        clearTimeout(to);
        reject(new Error(ev?.message || 'WS error'));
      });
    });

    connectMs = Math.max(0, nowMs() - t0);

    const pingMethod = opts.pingMethod ?? 'omni_ping';
    if (pingMethod) {
      const id = Math.floor(Math.random() * 1e9);
      const payload = JSON.stringify({
        jsonrpc: '2.0',
        id,
        method: pingMethod,
        params: opts.pingParams ?? {},
      });
      const t1 = nowMs();

      const pong = await new Promise<boolean>((resolve) => {
        let done = false;
        const to = setTimeout(() => {
          if (done) return;
          done = true;
          resolve(false);
          try { ws.close(); } catch {}
        }, timeoutMs);

        ws.addEventListener('message', (evt: MessageEvent) => {
          if (done) return;
          try {
            const msg = JSON.parse(String((evt as any).data));
            if (msg && msg.id === id) {
              clearTimeout(to);
              done = true;
              resolve(!isRpcError(msg));
              try { ws.close(); } catch {}
            }
          } catch {
            // ignore non-JSON frames
          }
        });

        try {
          ws.send(payload);
        } catch {
          clearTimeout(to);
          done = true;
          resolve(false);
          try { ws.close(); } catch {}
        }
      });

      if (pong) {
        pingMs = Math.max(0, nowMs() - t1);
      } else {
        // ping didn't round-trip; keep connect time as signal
        pingMs = undefined;
      }
    } else {
      // No ping requested; just close
      try { ws.close(); } catch {}
    }

    return {
      ok: true,
      url,
      connectMs,
      pingMs,
      timestamp: new Date().toISOString(),
    };
  } catch (e: any) {
    return {
      ok: false,
      url,
      connectMs,
      pingMs,
      error: errToString(e),
      timestamp: new Date().toISOString(),
    };
  }
}

/* ------------------------------- Combined probe ----------------------------- */

export interface HealthSummary {
  rpc: RpcHealthSample;
  ws?: WsHealthSample;
}

/**
 * Check both RPC and WS health.
 *
 * @param rpc RpcHealthOptions
 * @param ws  Optional WsHealthOptions (if omitted, will attempt to derive from rpc.rpcUrl)
 */
export async function checkHealth(
  rpc: RpcHealthOptions,
  ws?: WsHealthOptions,
): Promise<HealthSummary> {
  const [rpcSample, wsSample] = await Promise.all([
    rpcHealth(rpc),
    (async () => {
      try {
        return await wsHealth(
          ws ?? { rpcUrlForDerive: rpc.rpcUrl, timeoutMs: rpc.timeoutMs ?? 6000 },
        );
      } catch (e) {
        return undefined;
      }
    })(),
  ]);

  return { rpc: rpcSample, ws: wsSample };
}

/* --------------------------------- Example ---------------------------------- */
/*
(async () => {
  const summary = await checkHealth({ rpcUrl: 'https://rpc.devnet.animica.xyz' });
  console.log(summary);
})();
*/
