/**
 * Animica Explorer — JSON-RPC Client (fetch + retries)
 * -----------------------------------------------------------------------------
 * Small, production-ready JSON-RPC 2.0 client tailored for Explorer needs.
 * - Robust retries with exponential backoff + full jitter
 * - Request timeouts via AbortController
 * - Graceful JSON-RPC error handling with rich error types
 * - Batch calls with stable id correlation
 * - Works in modern browsers and Node (via global fetch or polyfill)
 */

import { inferRpcUrl } from './env';

export type JsonValue =
  | null
  | boolean
  | number
  | string
  | JsonValue[]
  | { [k: string]: JsonValue };

export interface JsonRpcRequest {
  jsonrpc: '2.0';
  id: number | string;
  method: string;
  params?: JsonValue | JsonValue[];
}

export interface JsonRpcSuccess<T = unknown> {
  jsonrpc: '2.0';
  id: number | string;
  result: T;
}

export interface JsonRpcErrorObj {
  code: number;
  message: string;
  data?: JsonValue;
}

export interface JsonRpcFailure {
  jsonrpc: '2.0';
  id: number | string | null;
  error: JsonRpcErrorObj;
}

export type JsonRpcResponse<T = unknown> = JsonRpcSuccess<T> | JsonRpcFailure;

export type BatchResponse = Array<JsonRpcResponse>;

export interface RpcClientOptions {
  /** Base URL of the JSON-RPC endpoint, e.g. "https://node.devnet.example/rpc" */
  url: string;

  /** Additional headers to send with each request (merged). */
  headers?: Record<string, string>;

  /** Maximum retry attempts (default: 3) */
  maxRetries?: number;

  /** Per-request timeout in milliseconds (default: 10_000) */
  timeoutMs?: number;

  /** Initial backoff delay in ms (default: 150) */
  baseDelayMs?: number;

  /** Max backoff delay in ms (default: 2_500) */
  maxDelayMs?: number;

  /**
   * Decide if a failure is retryable. Return true to retry.
   * Default: retries 429/5xx HTTP; network/timeout; JSON-RPC codes -32000..-32099 & -32603.
   */
  shouldRetry?: (ctx: {
    attempt: number;
    error?:
      | RpcError
      | HttpError
      | NetworkError
      | TimeoutError
      | ParseError
      | UnknownError;
    httpStatus?: number;
    rpcError?: JsonRpcErrorObj;
    method?: string;
  }) => boolean;
}

/* -------------------------------- Errors ---------------------------------- */

export class RpcError extends Error {
  readonly code: number;
  readonly data?: JsonValue;
  constructor(msg: string, code: number, data?: JsonValue) {
    super(msg);
    this.name = 'RpcError';
    this.code = code;
    this.data = data;
  }
}

export class HttpError extends Error {
  readonly status: number;
  readonly body?: string;
  constructor(status: number, body?: string) {
    super(`HTTP ${status}`);
    this.name = 'HttpError';
    this.status = status;
    this.body = body;
  }
}

export class TimeoutError extends Error {
  constructor(ms: number) {
    super(`Request timed out after ${ms} ms`);
    this.name = 'TimeoutError';
  }
}

export class NetworkError extends Error {
  constructor(msg = 'Network error') {
    super(msg);
    this.name = 'NetworkError';
  }
}

export class ParseError extends Error {
  constructor(msg = 'Failed to parse response') {
    super(msg);
    this.name = 'ParseError';
  }
}

export class UnknownError extends Error {
  constructor(msg = 'Unknown error') {
    super(msg);
    this.name = 'UnknownError';
  }
}

/* ------------------------------ Backoff utils ------------------------------ */

function sleep(ms: number) {
  return new Promise((res) => setTimeout(res, ms));
}

function expoJitterDelay(
  attempt: number,
  base: number,
  cap: number
): number {
  // attempt: 0..N
  const exp = Math.min(cap, base * 2 ** attempt);
  // Full jitter
  return Math.floor(Math.random() * exp);
}

/* ------------------------------- Type guards ------------------------------- */

function isJsonRpcError(x: any): x is JsonRpcFailure {
  return (
    x &&
    x.jsonrpc === '2.0' &&
    typeof x.error?.code === 'number' &&
    typeof x.error?.message === 'string'
  );
}

function isJsonRpcSuccess(x: any): x is JsonRpcSuccess {
  return x && x.jsonrpc === '2.0' && 'result' in x && !('error' in x);
}

/* --------------------------------- Client ---------------------------------- */

export class RpcClient {
  private url: string;
  private headers: Record<string, string>;
  private maxRetries: number;
  private timeoutMs: number;
  private baseDelayMs: number;
  private maxDelayMs: number;
  private shouldRetry: NonNullable<RpcClientOptions['shouldRetry']>;
  private seq: number;

  constructor(opts: RpcClientOptions) {
    if (!opts?.url) throw new Error('RpcClient: url is required');

    this.url = opts.url.replace(/\/+$/, ''); // trim trailing slash
    this.headers = {
      'Content-Type': 'application/json',
      Accept: 'application/json',
      ...(opts.headers ?? {}),
    };
    this.maxRetries = opts.maxRetries ?? 3;
    this.timeoutMs = opts.timeoutMs ?? 10_000;
    this.baseDelayMs = opts.baseDelayMs ?? 150;
    this.maxDelayMs = opts.maxDelayMs ?? 2_500;
    this.shouldRetry =
      opts.shouldRetry ??
      ((ctx) => {
        // Retry network/timeout
        if (ctx.error instanceof NetworkError || ctx.error instanceof TimeoutError) return true;
        // Retry on 429 or 5xx
        if (typeof ctx.httpStatus === 'number' && (ctx.httpStatus === 429 || ctx.httpStatus >= 500)) {
          return true;
        }
        // Retry on certain JSON-RPC server errors
        const code = ctx.rpcError?.code;
        if (typeof code === 'number') {
          if (code === -32603) return true; // internal error
          if (code <= -32000 && code >= -32099) return true; // server error range
        }
        return false;
      });

        // Seed the request id with a time-ish value to reduce collision in multi-tabs
    this.seq = Date.now() % 1_000_000;
  }

  setHeader(key: string, value: string) {
    this.headers[key] = value;
  }

  setAuthToken(token: string) {
    this.headers['Authorization'] = `Bearer ${token}`;
  }

  setUrl(url: string) {
    this.url = url.replace(/\/+$/, '');
  }

  private nextId(): number {
    // wrap at Number.MAX_SAFE_INTEGER to avoid precision issues
    this.seq = (this.seq + 1) % 9_007_199_254_740_000; // < MAX_SAFE_INTEGER
    return this.seq;
  }

  /**
   * Perform a single JSON-RPC call.
   */
  async call<T = unknown>(method: string, params?: JsonValue | JsonValue[]): Promise<T> {
    const id = this.nextId();
    const req: JsonRpcRequest = { jsonrpc: '2.0', id, method, ...(params !== undefined ? { params } : {}) };

    const res = await this.send<JsonRpcResponse<T>>(req, method);
    if (isJsonRpcSuccess(res)) return res.result as T;
    if (isJsonRpcError(res)) throw new RpcError(res.error.message, res.error.code, res.error.data);
    throw new UnknownError('Unexpected JSON-RPC response shape');
  }

  /**
   * Perform a batch JSON-RPC call. Returns results in the same order as calls[].
   */
  async batch<T = unknown>(calls: Array<{ method: string; params?: JsonValue | JsonValue[] }>): Promise<T[]> {
    if (calls.length === 0) return [];
    const reqs: JsonRpcRequest[] = calls.map((c) => ({
      jsonrpc: '2.0',
      id: this.nextId(),
      method: c.method,
      ...(c.params !== undefined ? { params: c.params } : {}),
    }));

    const res = await this.send<BatchResponse>(reqs, 'batch');
    if (!Array.isArray(res)) throw new ParseError('Batch response must be an array');

    // Map by id to preserve order
    const byId = new Map<number | string, JsonRpcResponse>();
    for (const r of res) {
      if (!r || r.jsonrpc !== '2.0' || typeof r.id === 'undefined') continue;
      byId.set(r.id as number | string, r);
    }

    return reqs.map((q) => {
      const r = byId.get(q.id);
      if (!r) throw new ParseError(`Missing batch response for id=${q.id}`);
      if (isJsonRpcSuccess(r)) return r.result as T;
      if (isJsonRpcError(r)) throw new RpcError(r.error.message, r.error.code, r.error.data);
      throw new UnknownError('Unexpected JSON-RPC response in batch');
    });
  }

  /* ------------------------------- Transport ------------------------------- */

  private async send<T = unknown>(
    payload: JsonRpcRequest | JsonRpcRequest[],
    methodLabel?: string
  ): Promise<T> {
    const attempts = Math.max(0, this.maxRetries);
    let lastErr:
      | RpcError
      | HttpError
      | NetworkError
      | TimeoutError
      | ParseError
      | UnknownError
      | undefined;

    for (let attempt = 0; attempt <= attempts; attempt++) {
      const controller = new AbortController();
      const timer = setTimeout(() => controller.abort(), this.timeoutMs);

      try {
        const res = await fetch(this.url, {
          method: 'POST',
          headers: this.headers,
          body: JSON.stringify(payload),
          signal: controller.signal,
        });

        clearTimeout(timer);

        const httpStatus = res.status;

        let text: string;
        try {
          text = await res.text();
        } catch {
          text = '';
        }

        if (!res.ok) {
          const httpErr = new HttpError(httpStatus, text);
          const retry = this.shouldRetry({ attempt, error: httpErr, httpStatus, method: methodLabel });
          if (retry && attempt < attempts) {
            await sleep(expoJitterDelay(attempt, this.baseDelayMs, this.maxDelayMs));
            continue;
          }
          throw httpErr;
        }

        // On success, parse JSON
        let parsed: unknown;
        try {
          parsed = text ? JSON.parse(text) : undefined;
        } catch (e) {
          throw new ParseError((e as Error)?.message || 'Invalid JSON');
        }

        // If single
        if (!Array.isArray(payload)) {
          const obj = parsed as JsonRpcResponse;
          if (isJsonRpcError(obj)) {
            const rpcErr = new RpcError(obj.error.message, obj.error.code, obj.error.data);
            const retry = this.shouldRetry({ attempt, error: rpcErr, rpcError: obj.error, method: methodLabel });
            if (retry && attempt < attempts) {
              await sleep(expoJitterDelay(attempt, this.baseDelayMs, this.maxDelayMs));
              continue;
            }
            throw rpcErr;
          }
          return obj as T;
        }

        // If batch
        return parsed as T;
      } catch (e: any) {
        clearTimeout(timer);

        // Distinguish errors for retry policy
        let err:
          | RpcError
          | HttpError
          | NetworkError
          | TimeoutError
          | ParseError
          | UnknownError;

        if (e?.name === 'AbortError') {
          err = new TimeoutError(this.timeoutMs);
        } else if (e instanceof RpcError || e instanceof HttpError || e instanceof ParseError) {
          err = e;
        } else if (e instanceof TypeError || e?.message?.includes('fetch failed')) {
          err = new NetworkError(e?.message);
        } else {
          err = new UnknownError(e?.message ?? 'Unknown failure');
        }

        lastErr = err;

        const retry = this.shouldRetry({ attempt, error: err, method: methodLabel });
        if (retry && attempt < attempts) {
          await sleep(expoJitterDelay(attempt, this.baseDelayMs, this.maxDelayMs));
          continue;
        }
        throw err;
      }
    }

    // This should be unreachable; included for type satisfaction.
    throw lastErr ?? new UnknownError('Exhausted retries with unknown error');
  }
}

/* ------------------------------ Convenience API ---------------------------- */

export function createRpc(opts: RpcClientOptions): RpcClient {
  return new RpcClient(opts);
}

/**
 * Simple helper that builds a client from a base URL and optional API key.
 * Example:
 *   const rpc = rpcFromEnv(import.meta.env.VITE_RPC_URL, import.meta.env.VITE_API_KEY);
 */
export function rpcFromEnv(url?: string, apiKey?: string): RpcClient {
  const baseUrl = url ?? inferRpcUrl();
  const client = new RpcClient({ url: baseUrl });
  if (apiKey) client.setAuthToken(apiKey);
  return client;
}
