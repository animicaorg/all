/**
 * WebSocket JSON-RPC 2.0 transport with auto-reconnect and resubscriptions.
 *
 * Design goals:
 *  - Works in browser and Node (pass a WebSocket ctor in Node).
 *  - Auto-reconnect with jittered backoff; pending requests are rejected on close.
 *  - Subscriptions are re-established after reconnect (client tracks them).
 *  - Minimal JSON-RPC request/response handling over WS.
 *
 * Usage (browser):
 *   const ws = createWsClient('ws://localhost:8545/ws')
 *   const unsub = await ws.subscribe('newHeads', undefined, (head) => console.log(head))
 *   const head = await ws.request('chain.getHead')
 *
 * Usage (Node):
 *   import WebSocket from 'isomorphic-ws'
 *   const ws = createWsClient('ws://localhost:8545/ws', { webSocketCtor: WebSocket })
 */

import type {
  RpcTransport,
  RequestOptions,
  JsonRpcId,
  JsonRpcRequest,
  JsonRpcResponse,
  JsonRpcSuccess,
  JsonRpcFailure
} from './index'
import { RpcError } from '../errors'

// ──────────────────────────────────────────────────────────────────────────────
// Options & public API
// ──────────────────────────────────────────────────────────────────────────────

export interface WsClientOptions {
  /**
   * Provide a WebSocket constructor (required in Node).
   * In the browser, globalThis.WebSocket is used by default.
   */
  webSocketCtor?: new (url: string, protocols?: string | string[]) => WebSocket
  /** Sec-WebSocket-Protocol(s) if your server uses them. */
  protocols?: string | string[]
  /**
   * Backoff options for auto-reconnect.
   * Defaults: enabled=true, minDelay=500ms, factor=1.7, maxDelay=10_000ms, full jitter.
   */
  reconnect?: {
    enabled?: boolean
    minDelay?: number
    factor?: number
    maxDelay?: number
    /** 'none' | 'full' */
    jitter?: 'none' | 'full'
  }
  /** Optional function to create JSON-RPC ids. Default: incrementing integer. */
  idFactory?: () => number | string | null
  /**
   * Application-level ping (JSON-RPC method) to keep connections alive through proxies.
   * If set, the client will periodically call this method (and ignore errors).
   */
  ping?: { method: string; intervalMs?: number }
  /**
   * Query parameters to append to the URL (e.g., API key).
   * In browsers you cannot set custom headers; query params are the usual alternative.
   */
  query?: Record<string, string | number | boolean>
}

export interface SubscriptionHandle {
  /** The server-returned subscription id (string or number). */
  id: string | number
  /** Topic/method used to subscribe (for re-subscription after reconnect). */
  topic: string
  /** Params originally passed when subscribing. */
  params: unknown
  /** Stop receiving updates and inform the server (best-effort on disconnect). */
  unsubscribe: () => Promise<void>
}

export interface WsClient extends RpcTransport {
  /** Subscribe to a server topic; re-subscribes automatically after reconnects. */
  subscribe<T = unknown>(topic: string, params: unknown, onMessage: (data: T) => void): Promise<SubscriptionHandle>
  /** Close the socket and stop auto-reconnect. */
  close(code?: number, reason?: string): void
  /** Current connection state. */
  readonly readyState: number
  /** Event hooks */
  on(event: 'open' | 'close' | 'reconnect' | 'error', handler: (...args: any[]) => void): () => void
}

export function createWsClient(url: string, opts?: WsClientOptions): WsClient {
  return new WsRpcClient(url, opts)
}

// ──────────────────────────────────────────────────────────────────────────────
// Implementation
// ──────────────────────────────────────────────────────────────────────────────

type Pending = {
  resolve: (v: any) => void
  reject: (err: any) => void
  method: string
}

type SubRecord = {
  topic: string
  params: unknown
  onMessage: (data: any) => void
  /** server subscription id (after successful subscribe) */
  subId?: string | number
}

class WsRpcClient implements WsClient {
  private baseUrl: string
  private ws?: WebSocket
  private WebSocketCtor: new (url: string, protocols?: string | string[]) => WebSocket
  private protocols?: string | string[]
  private reconnectCfg: Required<NonNullable<WsClientOptions['reconnect']>>
  private reconnecting = false
  private shouldReconnect = true
  private idFactory: () => JsonRpcId
  private nextId = 1
  private pingCfg?: { method: string; intervalMs: number }
  private pingTimer?: ReturnType<typeof setInterval>

  // request/response tracking
  private inflight = new Map<JsonRpcId, Pending>()
  // topic/subscription tracking (by client-local key)
  private subscriptions = new Map<string, SubRecord>()
  // server subId -> client-local key mapping
  private byServerId = new Map<string | number, string>()
  // listeners
  private listeners = new Map<string, Set<(...args: any[]) => void>>()

  constructor(url: string, opts?: WsClientOptions) {
    this.baseUrl = appendQuery(url, opts?.query)
    this.WebSocketCtor = opts?.webSocketCtor ?? (globalThis as any).WebSocket
    if (!this.WebSocketCtor) {
      throw new Error('No WebSocket constructor available. Pass { webSocketCtor } in Node environments.')
    }
    this.protocols = opts?.protocols
    const rc = opts?.reconnect ?? {}
    this.reconnectCfg = {
      enabled: rc.enabled !== false,
      minDelay: rc.minDelay ?? 500,
      factor: rc.factor ?? 1.7,
      maxDelay: rc.maxDelay ?? 10_000,
      jitter: rc.jitter ?? 'full'
    }
    this.idFactory = opts?.idFactory ?? (() => this.nextId++)
    if (opts?.ping?.method) {
      this.pingCfg = { method: opts.ping.method, intervalMs: opts.ping.intervalMs ?? 25_000 }
    }
    this.open()
  }

  get readyState(): number {
    return this.ws?.readyState ?? WebSocket.CLOSED
  }

  on(event: 'open' | 'close' | 'reconnect' | 'error', handler: (...args: any[]) => void): () => void {
    const set = this.listeners.get(event) ?? new Set()
    set.add(handler)
    this.listeners.set(event, set)
    return () => set.delete(handler)
  }

  private emit(event: 'open' | 'close' | 'reconnect' | 'error', ...args: any[]) {
    const set = this.listeners.get(event)
    if (!set) return
    for (const fn of set) {
      try {
        fn(...args)
      } catch {
        // ignore user handler errors
      }
    }
  }

  // ────────────────────────────────────────────────────────────────────────────
  // RPC Transport
  // ────────────────────────────────────────────────────────────────────────────

  async request<R = unknown, P = unknown>(method: string, params?: P, _opts?: RequestOptions): Promise<R> {
    if (!this.ws || this.ws.readyState !== WebSocket.OPEN) {
      throw new RpcError(-32000, 'WebSocket is not open')
    }
    const id = this.idFactory()
    const req: JsonRpcRequest = { jsonrpc: '2.0', method, params, id }
    const payload = JSON.stringify(req)
    return new Promise<R>((resolve, reject) => {
      this.inflight.set(id, { resolve, reject, method })
      try {
        this.ws!.send(payload)
      } catch (err) {
        this.inflight.delete(id)
        reject(err)
      }
    })
  }

  async subscribe<T = unknown>(topic: string, params: unknown, onMessage: (data: T) => void): Promise<SubscriptionHandle> {
    // Client-local key to track resubscribes (topic + stable index)
    const key = `${topic}:${Math.random().toString(36).slice(2)}`
    const rec: SubRecord = { topic, params, onMessage }
    this.subscriptions.set(key, rec)

    // Perform subscribe RPC (convention: 'subscribe' method; server returns subId)
    const subId = await this.request<string | number>('subscribe', { topic, params })
    rec.subId = subId
    this.byServerId.set(subId, key)

    const unsubscribe = async () => {
      // Mark as removed locally first to avoid resubscribe after reconnect.
      this.subscriptions.delete(key)
      if (rec.subId != null) this.byServerId.delete(rec.subId)
      try {
        // Best-effort: if socket is closed, server already dropped the sub.
        if (this.ws && this.ws.readyState === WebSocket.OPEN && rec.subId != null) {
          await this.request('unsubscribe', { subscription: rec.subId })
        }
      } catch {
        // ignore server-side errors during unsubscribe
      }
    }

    return { id: subId, topic, params, unsubscribe }
  }

  // ────────────────────────────────────────────────────────────────────────────
  // Connection management
  // ────────────────────────────────────────────────────────────────────────────

  close(code?: number, reason?: string) {
    this.shouldReconnect = false
    this.clearPing()
    try {
      this.ws?.close(code, reason)
    } catch {
      // noop
    }
  }

  private open() {
    this.ws = new this.WebSocketCtor(this.baseUrl, this.protocols)
    this.ws.addEventListener('open', () => {
      this.startPing()
      this.emit('open')
      if (this.reconnecting) {
        this.emit('reconnect')
        this.reconnecting = false
      }
      // Re-subscribe all known subscriptions.
      this.resubscribeAll().catch(() => {
        /* resubscribe failures are surfaced per-sub call already; no-op here */
      })
    })
    this.ws.addEventListener('message', (ev) => this.onMessage(ev))
    this.ws.addEventListener('error', (ev) => {
      this.emit('error', ev)
    })
    this.ws.addEventListener('close', () => {
      this.clearPing()
      this.failInflight(new RpcError(-32000, 'WebSocket closed'))
      this.emit('close')
      if (this.shouldReconnect && this.reconnectCfg.enabled) {
        this.scheduleReconnect()
      }
    })
  }

  private startPing() {
    if (!this.pingCfg) return
    this.clearPing()
    this.pingTimer = setInterval(async () => {
      if (!this.ws || this.ws.readyState !== WebSocket.OPEN) return
      try {
        await this.request(this.pingCfg!.method)
      } catch {
        // ignore ping failures
      }
    }, this.pingCfg.intervalMs)
  }

  private clearPing() {
    if (this.pingTimer) {
      clearInterval(this.pingTimer)
      this.pingTimer = undefined
    }
  }

  private async resubscribeAll() {
    // Re-establish each subscription with the server.
    for (const [key, rec] of this.subscriptions) {
      try {
        const subId = await this.request<string | number>('subscribe', {
          topic: rec.topic,
          params: rec.params
        })
        rec.subId = subId
        this.byServerId.set(subId, key)
      } catch (err) {
        // Keep local record so callers can decide to retry manually.
        // Surface an error event.
        this.emit('error', new Error(`Failed to resubscribe to ${rec.topic}: ${String(err)}`))
      }
    }
  }

  private scheduleReconnect() {
    this.reconnecting = true
    let attempt = 0
    const doReconnect = () => {
      if (!this.shouldReconnect) return
      const delay = computeBackoff(this.reconnectCfg, attempt++)
      setTimeout(() => {
        try {
          this.open()
        } catch {
          // Opening may throw synchronously if ctor fails; schedule next attempt.
          doReconnect()
        }
      }, delay)
    }
    doReconnect()
  }

  // ────────────────────────────────────────────────────────────────────────────
  // Message path
  // ────────────────────────────────────────────────────────────────────────────

  private onMessage(ev: MessageEvent) {
    let msg: any
    try {
      msg = typeof ev.data === 'string' ? JSON.parse(ev.data) : JSON.parse(String(ev.data))
    } catch {
      // Ignore malformed JSON
      return
    }

    // Handle batch frames
    if (Array.isArray(msg)) {
      for (const item of msg) this.handleFrame(item)
      return
    }
    this.handleFrame(msg)
  }

  private handleFrame(frame: any) {
    // JSON-RPC response (success/failure) with id
    if (frame && typeof frame === 'object' && 'jsonrpc' in frame && 'id' in frame) {
      const pending = this.inflight.get(frame.id as JsonRpcId)
      if (!pending) return // unknown or already timed out
      this.inflight.delete(frame.id as JsonRpcId)
      if (isSuccess(frame)) {
        pending.resolve(frame.result)
      } else if (isFailure(frame)) {
        pending.reject(new RpcError(frame.error.code, frame.error.message, frame.error.data))
      } else {
        pending.reject(new RpcError(-32603, 'Invalid JSON-RPC response frame'))
      }
      return
    }

    // Subscription notifications (Ethereum-like)
    // { jsonrpc:"2.0", method:"<topic>", params:{ subscription:<id>, result:<payload> } }
    const topic: string | undefined = frame?.method
    const subId: string | number | undefined = frame?.params?.subscription
    const payload = frame?.params?.result ?? frame?.params
    if (subId !== undefined) {
      const key = this.byServerId.get(subId)
      if (!key) return
      const rec = this.subscriptions.get(key)
      if (!rec) return
      try {
        rec.onMessage(payload)
      } catch {
        // user handler errors are ignored
      }
      return
    }

    // Fallback: topic-only dispatch (no subscription id in params)
    if (topic) {
      for (const [, rec] of this.subscriptions) {
        if (rec.topic === topic) {
          try {
            rec.onMessage(payload)
          } catch {
            // ignore
          }
        }
      }
    }
  }

  private failInflight(err: any) {
    for (const [id, p] of this.inflight) {
      p.reject(err instanceof Error ? err : new Error(String(err)))
      this.inflight.delete(id)
    }
  }
}

// ──────────────────────────────────────────────────────────────────────────────
// Helpers
// ──────────────────────────────────────────────────────────────────────────────

function isSuccess(x: any): x is JsonRpcSuccess {
  return x?.jsonrpc === '2.0' && 'result' in x
}
function isFailure(x: any): x is JsonRpcFailure {
  return x?.jsonrpc === '2.0' && 'error' in x
}

function appendQuery(u: string, q?: Record<string, string | number | boolean>): string {
  if (!q || Object.keys(q).length === 0) return u
  const url = new URL(u, typeof window !== 'undefined' ? window.location.href : 'http://localhost')
  for (const [k, v] of Object.entries(q)) url.searchParams.set(k, String(v))
  return url.toString()
}

function computeBackoff(
  cfg: Required<NonNullable<WsClientOptions['reconnect']>>,
  attempt: number
): number {
  const raw = Math.min(cfg.maxDelay, cfg.minDelay * Math.pow(cfg.factor, attempt))
  if (cfg.jitter === 'full') {
    return Math.floor(Math.random() * raw)
  }
  return Math.floor(raw)
}

export default createWsClient
