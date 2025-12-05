/**
 * JSON-RPC 2.0 HTTP client for the wallet background service worker.
 * - Deterministic request IDs
 * - AbortController timeouts
 * - Exponential backoff with jitter on retryable failures (network/HTTP 5xx/429)
 * - Typed RpcError for JSON-RPC error objects
 *
 * WebSocket subscriptions live in `network/subscriptions.ts`.
 */

import type { Network } from "./networks";

let nextId = 1;

export interface RpcClientOptions {
  /** Full HTTP(S) endpoint URL */
  url: string;
  /** Per-call timeout (ms). Default: 10_000 */
  timeoutMs?: number;
  /** Max retry attempts on retryable errors. Default: 3 */
  maxRetries?: number;
  /** Base backoff (ms). Jitter is applied. Default: 250 */
  baseBackoffMs?: number;
  /** Optional extra headers to send (CORS must allow) */
  headers?: Record<string, string>;
}

type Json = null | boolean | number | string | Json[] | { [k: string]: Json };

export interface JsonRpcRequest {
  jsonrpc: "2.0";
  method: string;
  params?: Json | Json[];
  id: number | string;
}

export interface JsonRpcSuccess<T = Json> {
  jsonrpc: "2.0";
  id: number | string;
  result: T;
}

export interface JsonRpcErrorPayload {
  code: number;
  message: string;
  data?: Json;
}

export interface JsonRpcFailure {
  jsonrpc: "2.0";
  id: number | string | null;
  error: JsonRpcErrorPayload;
}

export type JsonRpcResponse<T = Json> = JsonRpcSuccess<T> | JsonRpcFailure;

export class RpcError extends Error {
  readonly code: number;
  readonly data?: Json;

  constructor(message: string, code: number, data?: Json) {
    super(message);
    this.name = "RpcError";
    this.code = code;
    this.data = data;
  }

  static from(payload: JsonRpcErrorPayload): RpcError {
    return new RpcError(payload.message, payload.code, payload.data);
  }
}

export class HttpError extends Error {
  readonly status: number;
  constructor(message: string, status: number) {
    super(message);
    this.name = "HttpError";
    this.status = status;
  }
}

export class RpcClient {
  private readonly url: string;
  private readonly timeoutMs: number;
  private readonly maxRetries: number;
  private readonly baseBackoffMs: number;
  private readonly headers: Record<string, string>;

  constructor(opts: RpcClientOptions) {
    this.url = opts.url;
    this.timeoutMs = opts.timeoutMs ?? 10_000;
    this.maxRetries = opts.maxRetries ?? 3;
    this.baseBackoffMs = opts.baseBackoffMs ?? 250;
    this.headers = {
      "content-type": "application/json",
      accept: "application/json",
      ...(opts.headers ?? {}),
    };
  }

  /** Single JSON-RPC call with typed result. */
  async call<T = Json>(method: string, params?: Json | Json[]): Promise<T> {
    const req: JsonRpcRequest = {
      jsonrpc: "2.0",
      method,
      params,
      id: nextId++,
    };
    const res = await this._postWithRetries<JsonRpcResponse<T>>(req, method);
    if ("error" in res) {
      throw RpcError.from(res.error);
    }
    return res.result;
  }

  /**
   * Batch call.
   * Returns results in the same order as input items (matched by id mapping).
   */
  async batch<T = Json>(
    items: { method: string; params?: Json | Json[] }[]
  ): Promise<(T | RpcError)[]> {
    const reqs: JsonRpcRequest[] = items.map((it) => ({
      jsonrpc: "2.0",
      method: it.method,
      params: it.params,
      id: nextId++,
    }));

    const methodHint = `batch:${items.map((it) => it.method).join(",")}`;
    const res = await this._postWithRetries<JsonRpcResponse<T>[]>(reqs, methodHint);

    // Map by id for stable ordering
    const byId = new Map<number | string, JsonRpcResponse<T>>();
    for (const r of res) byId.set(r.id as number | string, r);

    return reqs.map((req) => {
      const r = byId.get(req.id)!;
      if (!r) return new RpcError("Missing batch response item", -32000);
      if ("error" in r) return RpcError.from(r.error);
      return r.result;
    });
  }

  /** Lightweight health check (method name may vary per node) */
  async health(): Promise<boolean> {
    try {
      // Prefer a cheap head/height method if your node supports it.
      // Fallback to any trivial method that exists.
      await this.call("omni_ping");
      return true;
    } catch {
      return false;
    }
  }

  // Internal POST + retries
  private async _postWithRetries<T = any>(body: any, methodHint?: string): Promise<T> {
    let attempt = 0;
    // eslint-disable-next-line no-constant-condition
    while (true) {
      attempt += 1;
      try {
        return await this._postOnce<T>(body, methodHint);
      } catch (err: any) {
        if (attempt > this.maxRetries || !isRetryable(err)) {
          throw err;
        }
        const delay = backoffWithJitter(this.baseBackoffMs, attempt);
        await sleep(delay);
      }
    }
  }

  private async _postOnce<T = any>(body: any, methodHint?: string): Promise<T> {
    const ctrl = new AbortController();
    const t = setTimeout(() => ctrl.abort(), this.timeoutMs);

    try {
      const resp = await fetch(this.url, {
        method: "POST",
        headers: this.headers,
        body: JSON.stringify(body),
        cache: "no-store",
        credentials: "omit",
        redirect: "follow",
        mode: "cors",
        signal: ctrl.signal,
      });

      if (!resp.ok) {
        // Bubble HTTP errors with status for retry policy and log enough context to debug routing issues.
        console.warn(
          `[rpc] HTTP ${resp.status} from ${this.url} for method ${methodHint ?? "unknown"}`,
          {
            url: this.url,
            status: resp.status,
            method: methodHint,
          }
        );
        throw new HttpError(`HTTP ${resp.status} from JSON-RPC`, resp.status);
      }

      // Some nodes may return empty body on 204; treat as error.
      const text = await resp.text();
      if (!text) throw new HttpError("Empty JSON-RPC response body", resp.status);

      let parsed: any;
      try {
        parsed = JSON.parse(text);
      } catch {
        throw new HttpError("Invalid JSON in JSON-RPC response", resp.status);
      }
      return parsed as T;
    } finally {
      clearTimeout(t);
    }
  }
}

/** Create a client from a Network record. */
export function makeRpcClientForNetwork(net: Network, opts?: Partial<RpcClientOptions>): RpcClient {
  return new RpcClient({
    url: net.rpcHttp,
    timeoutMs: opts?.timeoutMs,
    maxRetries: opts?.maxRetries,
    baseBackoffMs: opts?.baseBackoffMs,
    headers: opts?.headers,
  });
}

/** Convenience one-off call using a Network. */
export async function rpcCall<T = Json>(
  net: Network,
  method: string,
  params?: Json | Json[],
  opts?: Partial<RpcClientOptions>
): Promise<T> {
  const client = makeRpcClientForNetwork(net, opts);
  return client.call<T>(method, params);
}

/** Retry policy: network errors, HTTP 5xx, 429 Too Many Requests */
function isRetryable(err: unknown): boolean {
  if (!err) return false;
  // Abort due to timeout shouldn't be retried endlessly, but allow a few attempts.
  if (err instanceof DOMException && err.name === "AbortError") return true;
  if (err instanceof TypeError) return true; // fetch network error
  if (err instanceof HttpError) {
    const s = err.status;
    return s === 429 || (s >= 500 && s <= 599);
  }
  return false;
}

function backoffWithJitter(baseMs: number, attempt: number): number {
  // Exponential: base * 2^(attempt-1), capped, with full jitter
  const max = Math.min(10_000, baseMs * Math.pow(2, attempt - 1));
  return Math.floor(Math.random() * (max - baseMs + 1) + baseMs);
}

function sleep(ms: number): Promise<void> {
  return new Promise((r) => setTimeout(r, ms));
}

export default RpcClient;
