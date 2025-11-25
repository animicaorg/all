/* eslint-disable no-restricted-globals */
/**
 * simulation.worker.ts
 * Off-thread JSON-RPC "call" / "estimate" / generic request helper for the wallet.
 *
 * Why a worker?
 *  - Keeps heavy simulations and long-poll calls off the service worker's hot path.
 *  - Avoids blocking UI when dapps spam call/estimate.
 *
 * Protocol
 *  Request:
 *    { id, op, ...payload }
 *
 *  Supported ops:
 *    - "rpc.request"    { rpcUrl: string, method: string, params?: any[], timeoutMs?: number, headers?: Record<string,string> }
 *    - "rpc.batch"      { rpcUrl: string, batch: { method: string, params?: any[] }[], timeoutMs?: number, headers?: Record<string,string> }
 *    - "call"           { rpcUrl: string, call: RpcCall, blockTag?: string, timeoutMs?: number, headers?: Record<string,string> }
 *    - "estimateGas"    { rpcUrl: string, call: RpcCall, timeoutMs?: number, headers?: Record<string,string> }
 *    - "ping"           {}
 *
 *  RpcCall shape (chain-agnostic; map to node's call schema):
 *    {
 *      from?: string,
 *      to: string,
 *      data?: string,      // hex 0x...
 *      value?: string,     // hex or decimal string
 *      gas?: string|number,
 *      nonce?: string|number
 *    }
 *
 * Response:
 *    { id, ok: true, result } | { id, ok: false, error }
 */

export {};

type RpcCall = {
  from?: string;
  to: string;
  data?: string;
  value?: string;
  gas?: string | number;
  nonce?: string | number;
};

type Req =
  | { id: string; op: 'rpc.request'; rpcUrl: string; method: string; params?: any[]; timeoutMs?: number; headers?: Record<string, string> }
  | { id: string; op: 'rpc.batch'; rpcUrl: string; batch: { method: string; params?: any[] }[]; timeoutMs?: number; headers?: Record<string, string> }
  | { id: string; op: 'call'; rpcUrl: string; call: RpcCall; blockTag?: string; timeoutMs?: number; headers?: Record<string, string> }
  | { id: string; op: 'estimateGas'; rpcUrl: string; call: RpcCall; timeoutMs?: number; headers?: Record<string, string> }
  | { id: string; op: 'ping' };

type ResOk = { id: string; ok: true; result: unknown };
type ResErr = { id: string; ok: false; error: string };

declare const self: DedicatedWorkerGlobalScope;

function ok(id: string, result: unknown): void {
  const msg: ResOk = { id, ok: true, result };
  // @ts-expect-error worker global typing
  self.postMessage(msg);
}

function err(id: string, e: unknown): void {
  const message =
    e instanceof Error ? `${e.name}: ${e.message}` : typeof e === 'string' ? e : JSON.stringify(e);
  const msg: ResErr = { id, ok: false, error: message };
  // @ts-expect-error worker global typing
  self.postMessage(msg);
}

let _reqId = 1;
function nextJsonRpcId(): number {
  _reqId = (_reqId + 1) | 0;
  if (_reqId <= 0) _reqId = 1;
  return _reqId;
}

async function postJSON<T = unknown>(
  url: string,
  body: unknown,
  timeoutMs = 12000,
  headers?: Record<string, string>,
): Promise<T> {
  const ctrl = new AbortController();
  const t = setTimeout(() => ctrl.abort(), timeoutMs);
  try {
    const res = await fetch(url, {
      method: 'POST',
      headers: {
        'content-type': 'application/json',
        ...(headers || {}),
      },
      body: JSON.stringify(body),
      signal: ctrl.signal,
      cache: 'no-store',
    });
    if (!res.ok) {
      const text = await res.text().catch(() => '');
      throw new Error(`HTTP ${res.status}: ${text || res.statusText}`);
    }
    return (await res.json()) as T;
  } finally {
    clearTimeout(t);
  }
}

type JsonRpcResponse<T = any> = { jsonrpc: '2.0'; id: number | string | null; result?: T; error?: { code: number; message: string; data?: any } };

async function rpcRequest(
  rpcUrl: string,
  method: string,
  params?: any[],
  timeoutMs?: number,
  headers?: Record<string, string>,
): Promise<any> {
  const id = nextJsonRpcId();
  const resp = await postJSON<JsonRpcResponse>(rpcUrl, { jsonrpc: '2.0', id, method, params: params ?? [] }, timeoutMs, headers);
  if ('error' in resp && resp.error) {
    const data = resp.error.data ? `; data=${typeof resp.error.data === 'string' ? resp.error.data : JSON.stringify(resp.error.data)}` : '';
    throw new Error(`RPC ${method} error ${resp.error.code}: ${resp.error.message}${data}`);
  }
  return resp.result;
}

async function rpcBatch(
  rpcUrl: string,
  batch: { method: string; params?: any[] }[],
  timeoutMs?: number,
  headers?: Record<string, string>,
): Promise<any[]> {
  const payload = batch.map((b) => ({
    jsonrpc: '2.0' as const,
    id: nextJsonRpcId(),
    method: b.method,
    params: b.params ?? [],
  }));
  const resps = await postJSON<JsonRpcResponse[]>(rpcUrl, payload, timeoutMs, headers);
  // Map back by id ordering (assumes node returns in same order; if not, we align by id)
  const byId = new Map<number | string | null, JsonRpcResponse>();
  for (const r of resps) byId.set(r.id, r);
  return payload.map((p) => {
    const r = byId.get(p.id)!;
    if (!r) throw new Error(`Missing response for id=${String(p.id)}`);
    if (r.error) throw new Error(`RPC ${p.method} error ${r.error.code}: ${r.error.message}`);
    return r.result;
  });
}

async function handle(req: Req): Promise<void> {
  const { id, op } = req;
  try {
    switch (op) {
      case 'ping': {
        ok(id, { pong: true, ts: Date.now() });
        return;
      }
      case 'rpc.request': {
        const out = await rpcRequest(req.rpcUrl, req.method, req.params, req.timeoutMs, req.headers);
        ok(id, out);
        return;
      }
      case 'rpc.batch': {
        const out = await rpcBatch(req.rpcUrl, req.batch, req.timeoutMs, req.headers);
        ok(id, out);
        return;
      }
      case 'call': {
        // Common chain-agnostic "call" → maps to node method "call" with [call, blockTag]
        const params = [req.call, req.blockTag ?? 'latest'];
        const out = await rpcRequest(req.rpcUrl, 'call', params, req.timeoutMs, req.headers);
        ok(id, out);
        return;
      }
      case 'estimateGas': {
        const out = await rpcRequest(req.rpcUrl, 'estimateGas', [req.call], req.timeoutMs, req.headers);
        ok(id, out);
        return;
      }
      default:
        throw new Error(`Unsupported op: ${op as string}`);
    }
  } catch (e) {
    err(id, e);
  }
}

self.addEventListener('message', (ev: MessageEvent<Req>) => {
  void handle(ev.data);
});
