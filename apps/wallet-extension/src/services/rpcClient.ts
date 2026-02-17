import { clearTimeoutFn, fetchFn, setTimeoutFn } from '../runtime/env';
import { stringifySafe } from '../core/rpc/safeJson';

export type RpcErrorObject = { code?: number; message?: string; data?: unknown };
export type RpcEnvelope = { jsonrpc?: string; id?: number | string | null; result?: unknown; error?: RpcErrorObject };

export type RpcCallOutcome = {
  ok: boolean;
  method: string;
  params: unknown;
  httpStatus?: number;
  durationMs: number;
  response?: RpcEnvelope;
  networkError?: string;
  protocolError?: string;
};

export type DiscoverCache = {
  methods: string[];
  fetchedAt: number;
};

const discoverCache = new Map<string, DiscoverCache>();
const DISCOVER_TTL_MS = 60_000;

let reqId = 1;

function nextId(): number {
  const v = reqId;
  reqId = reqId >= 2_000_000_000 ? 1 : reqId + 1;
  return v;
}

function getFetch(): typeof fetch {
  if (typeof fetchFn !== 'function') throw new Error('Fetch API is unavailable in this runtime');
  return fetchFn;
}

function getSetTimeout(): typeof setTimeout {
  if (typeof setTimeoutFn !== 'function') throw new Error('setTimeout is unavailable in this runtime');
  return setTimeoutFn;
}

function getClearTimeout(): typeof clearTimeout {
  if (typeof clearTimeoutFn !== 'function') throw new Error('clearTimeout is unavailable in this runtime');
  return clearTimeoutFn;
}

function isNetworkError(error: unknown): boolean {
  const msg = error instanceof Error ? error.message : String(error);
  return /network|fetch|timeout|abort|failed to fetch|ecconn|econn|socket|dns/i.test(msg);
}

export async function rpcCall(
  rpcUrl: string,
  method: string,
  params: unknown,
  options: { timeoutMs?: number; retryNetworkOnce?: boolean } = {},
): Promise<RpcCallOutcome> {
  const fetchImpl = getFetch();
  const setTimeoutImpl = getSetTimeout();
  const clearTimeoutImpl = getClearTimeout();
  const timeoutMs = options.timeoutMs ?? 20_000;

  const doCall = async (): Promise<RpcCallOutcome> => {
    const startedAt = Date.now();
    const controller = new AbortController();
    const timer = setTimeoutImpl(() => controller.abort(), timeoutMs);
    try {
      const payload = { jsonrpc: '2.0' as const, id: nextId(), method, params };
      const response = await fetchImpl(rpcUrl, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: stringifySafe(payload),
        signal: controller.signal,
      });

      const text = await response.text();
      let parsed: RpcEnvelope | undefined;
      try {
        parsed = text ? (JSON.parse(text) as RpcEnvelope) : undefined;
      } catch {
        return {
          ok: false,
          method,
          params,
          durationMs: Date.now() - startedAt,
          httpStatus: response.status,
          protocolError: `Invalid JSON-RPC response body: ${text.slice(0, 400)}`,
        };
      }

      if (!parsed || (typeof parsed !== 'object')) {
        return {
          ok: false,
          method,
          params,
          durationMs: Date.now() - startedAt,
          httpStatus: response.status,
          protocolError: 'Malformed JSON-RPC response object',
        };
      }

      if (!('result' in parsed) && !('error' in parsed)) {
        return {
          ok: false,
          method,
          params,
          durationMs: Date.now() - startedAt,
          httpStatus: response.status,
          response: parsed,
          protocolError: 'JSON-RPC response missing both result and error',
        };
      }

      return {
        ok: !parsed.error,
        method,
        params,
        durationMs: Date.now() - startedAt,
        httpStatus: response.status,
        response: parsed,
      };
    } catch (error) {
      return {
        ok: false,
        method,
        params,
        durationMs: Date.now() - startedAt,
        networkError: error instanceof Error ? error.message : String(error),
      };
    } finally {
      clearTimeoutImpl(timer);
    }
  };

  const first = await doCall();
  if (first.ok) return first;
  if (!options.retryNetworkOnce || !first.networkError) return first;
  if (!isNetworkError(first.networkError)) return first;
  return doCall();
}

export async function discoverMethods(rpcUrl: string, timeoutMs: number = 20_000): Promise<string[]> {
  const cached = discoverCache.get(rpcUrl);
  if (cached && Date.now() - cached.fetchedAt < DISCOVER_TTL_MS) return cached.methods;

  const discover = await rpcCall(rpcUrl, 'rpc.discover', [], { timeoutMs, retryNetworkOnce: true });
  let methods: string[] = [];

  if (discover.response?.result && typeof discover.response.result === 'object') {
    const result = discover.response.result as { methods?: Array<{ name?: string }> };
    if (Array.isArray(result.methods)) {
      methods = result.methods.map((m) => m?.name).filter((m): m is string => typeof m === 'string');
    }
  }

  if (methods.length === 0) {
    const listed = await rpcCall(rpcUrl, 'rpc.listMethods', [], { timeoutMs, retryNetworkOnce: true });
    if (Array.isArray(listed.response?.result)) {
      methods = listed.response?.result.filter((x): x is string => typeof x === 'string') ?? [];
    }
  }

  discoverCache.set(rpcUrl, { methods, fetchedAt: Date.now() });
  return methods;
}

export async function healthProbe(rpcUrl: string, timeoutMs: number = 8_000): Promise<RpcCallOutcome> {
  const probes = ['node.ping', 'node_ping', 'chain.getHead'];
  let last: RpcCallOutcome | undefined;
  for (const method of probes) {
    const out = await rpcCall(rpcUrl, method, [], { timeoutMs, retryNetworkOnce: true });
    last = out;
    if (out.ok) return out;
    const code = out.response?.error?.code;
    if (code === -32601) continue;
    if (out.networkError) continue;
  }
  return last ?? {
    ok: false,
    method: 'node.ping',
    params: [],
    durationMs: 0,
    protocolError: 'RPC probe failed without response',
  };
}
