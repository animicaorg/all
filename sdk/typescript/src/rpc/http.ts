/**
 * HTTP JSON-RPC 2.0 transport (fetch-based) with retries & AbortSignal support.
 *
 * Usage:
 *   import { createHttpClient } from '@animica/sdk/rpc'
 *   const rpc = createHttpClient('http://localhost:8545/rpc')
 *   const head = await rpc.request<Head>('chain.getHead')
 *
 * Features:
 *  - Exponential backoff with jitter (429/5xx aware, honors Retry-After)
 *  - Per-attempt timeout and external AbortSignal
 *  - Batch requests with stable ordering
 *  - Strong typing for request/response envelopes
 */

import {
  RpcTransport,
  RequestOptions,
  JsonRpcResponse,
  isJsonRpcFailure,
  isJsonRpcSuccess
} from './index'
import {
  fetchWithRetry,
  HttpError,
  AbortError,
  mergeSignals,
  type FetchRetryOptions
} from '../utils/retry'
import { RpcError } from '../errors'

/** Options for the HTTP JSON-RPC client. */
export interface HttpClientOptions {
  /** Extra headers to send with every request (e.g., API keys). */
  headers?: Record<string, string>
  /**
   * Retry settings. Enable/disable and tune backoff/jitter.
   * Defaults: enabled=true, minDelay=200ms, factor=2, maxDelay=10s, full jitter, retries=5.
   */
  retry?: (FetchRetryOptions & { enabled?: boolean }) | undefined
  /**
   * Default per-attempt timeout (ms). Can be overridden per call via RequestOptions.timeoutMs.
   * Default: 15_000 ms.
   */
  timeoutMs?: number
  /**
   * Function to create JSON-RPC ids. Default is an incrementing integer counter.
   */
  idFactory?: () => number | string | null
}

/** Callable tuple used by batch(). */
export interface RpcCall<P = unknown> {
  method: string
  params?: P
}

/** Factory to create an HTTP JSON-RPC transport bound to a base URL. */
export function createHttpClient(baseUrl: string, opts?: HttpClientOptions): HttpClient {
  return new HttpClient(baseUrl, opts)
}

export class HttpClient implements RpcTransport {
  private baseUrl: string
  private headers: Record<string, string>
  private retry: FetchRetryOptions & { enabled: boolean }
  private timeoutMs: number
  private idFactory: () => number | string | null
  private seq = 1

  constructor(baseUrl: string, opts?: HttpClientOptions) {
    this.baseUrl = normalizeUrl(baseUrl)
    this.headers = {
      'content-type': 'application/json',
      accept: 'application/json',
      ...(opts?.headers ?? {})
    }
    const r = opts?.retry ?? {}
    this.retry = {
      enabled: r.enabled !== false,
      retries: r.retries ?? 5,
      minDelay: r.minDelay ?? 200,
      factor: r.factor ?? 2,
      maxDelay: r.maxDelay ?? 10_000,
      jitter: r.jitter ?? 'full',
      honorRetryAfter: r.honorRetryAfter ?? true,
      retryMethods: r.retryMethods ?? ['POST'] // JSON-RPC POSTs are idempotent per id; server must de-duplicate
    }
    this.timeoutMs = opts?.timeoutMs ?? 15_000
    this.idFactory = opts?.idFactory ?? (() => this.seq++)
  }

  /** Perform a single JSON-RPC request and return the typed result. */
  async request<R = unknown, P = unknown>(method: string, params?: P, opts?: RequestOptions): Promise<R> {
    const id = this.idFactory()
    const payload = makeSinglePayload(method, params, id)
    const signal = mergeSignals([opts?.signal])

    const response = await fetchWithRetry(
      this.baseUrl,
      {
        method: 'POST',
        headers: this.headers,
        body: JSON.stringify(payload),
        signal
      },
      this.retry.enabled
        ? { ...this.retry, attemptTimeoutMs: opts?.timeoutMs ?? this.timeoutMs, signal }
        : { retries: 0, attemptTimeoutMs: opts?.timeoutMs ?? this.timeoutMs, signal }
    )

    const json = await parseJson(response)

    // Some servers may respond with a batch even for single requests; handle permissively.
    if (Array.isArray(json)) {
      const match = json.find((x) => (x as any)?.id === id) as JsonRpcResponse<R> | undefined
      if (!match) throw new RpcError(-32603, 'Invalid batch response: missing id for single request')
      if (isJsonRpcSuccess<R>(match)) return match.result
      if (isJsonRpcFailure(match)) throw new RpcError(match.error.code, match.error.message, match.error.data)
      throw new RpcError(-32603, 'Invalid JSON-RPC response shape')
    }

    const obj = json as JsonRpcResponse<R>
    if (isJsonRpcSuccess<R>(obj)) return obj.result
    if (isJsonRpcFailure(obj)) throw new RpcError(obj.error.code, obj.error.message, obj.error.data)
    throw new RpcError(-32603, 'Invalid JSON-RPC response shape')
  }

  /**
   * Perform a batch of JSON-RPC calls.
   * Returns results in the same order as calls[].
   */
  async batch(calls: ReadonlyArray<RpcCall>, opts?: RequestOptions): Promise<unknown[]> {
    if (!Array.isArray(calls) || calls.length === 0) return []
    const ids = calls.map(() => this.idFactory())
    const payload = calls.map((c, i) => makeSinglePayload(c.method, c.params, ids[i]))

    const signal = mergeSignals([opts?.signal])
    const response = await fetchWithRetry(
      this.baseUrl,
      {
        method: 'POST',
        headers: this.headers,
        body: JSON.stringify(payload),
        signal
      },
      this.retry.enabled
        ? { ...this.retry, attemptTimeoutMs: opts?.timeoutMs ?? this.timeoutMs, signal }
        : { retries: 0, attemptTimeoutMs: opts?.timeoutMs ?? this.timeoutMs, signal }
    )

    const json = await parseJson(response)
    if (!Array.isArray(json)) throw new RpcError(-32603, 'Batch request expected array response')

    // Map responses by id for stable ordering
    const byId = new Map<any, JsonRpcResponse>()
    for (const r of json) byId.set((r as any)?.id, r)

    return ids.map((id) => {
      const item = byId.get(id) as JsonRpcResponse | undefined
      if (!item) throw new RpcError(-32603, `Missing response for id ${String(id)}`)
      if (isJsonRpcSuccess(item)) return item.result
      if (isJsonRpcFailure(item)) throw new RpcError(item.error.code, item.error.message, item.error.data)
      throw new RpcError(-32603, 'Invalid JSON-RPC response shape in batch')
    })
  }

  /** Create a shallow-cloned client with additional headers (e.g., new API key). */
  withHeaders(headers: Record<string, string>): HttpClient {
    return new HttpClient(this.baseUrl, {
      headers: { ...this.headers, ...headers },
      retry: this.retry,
      timeoutMs: this.timeoutMs,
      idFactory: this.idFactory
    })
  }
}

// ──────────────────────────────────────────────────────────────────────────────
// Helpers
// ──────────────────────────────────────────────────────────────────────────────

function makeSinglePayload<P = unknown>(method: string, params: P | undefined, id: number | string | null) {
  const payload: any = {
    jsonrpc: '2.0',
    method,
    id
  }
  if (params !== undefined) payload['params'] = params
  return payload
}

async function parseJson(res: Response): Promise<unknown> {
  const text = await res.text()
  try {
    // JSON-RPC forbids BigInt, so plain JSON.parse is fine here.
    return JSON.parse(text)
  } catch (e) {
    // Surface richer diagnostics while preserving original HTTP context.
    const hint = text && text.length < 2048 ? `; body="${text}"` : ''
    throw new HttpError(`Invalid JSON from server${hint}`, res.status, res.statusText, text)
  }
}

function normalizeUrl(u: string): string {
  // Accept base URLs with or without trailing slashes; do not mutate query/hash.
  // For JSON-RPC we almost always POST to the exact baseUrl.
  return u.replace(/\s+/g, '')
}

export type { FetchRetryOptions } from '../utils/retry'
export { HttpError, AbortError } from '../utils/retry'
export { RpcError } from '../errors'
export default createHttpClient
