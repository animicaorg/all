/**
 * Animica Explorer — WebSocket Subscriptions (auto-reconnect)
 * -----------------------------------------------------------------------------
 * Production-ready JSON-RPC over WebSocket client with:
 *  - Auto-reconnect (exponential backoff + jitter)
 *  - Resubscription of active streams on reconnect
 *  - Heartbeats & idle-timeout detection
 *  - Safe message routing and backpressure-friendly send queue
 *
 * Default JSON-RPC subscription contract (configurable):
 *   -> request:  {"jsonrpc":"2.0","id":N,"method":"subscribe","params":[<topic>, <params?>]}
 *   <- success:  {"jsonrpc":"2.0","id":N,"result":"<subId>"}
 *   <- notify:   {"jsonrpc":"2.0","method":"subscription","params":{"subscription":"<subId>","result":{...}}}
 *   -> unsubscribe: {"jsonrpc":"2.0","id":N,"method":"unsubscribe","params":["<subId>"]}
 *
 * If your node uses different method names or shapes, configure via WsClientOptions.
 */

import { inferWsUrl } from './env';

export type JsonValue =
  | null
  | boolean
  | number
  | string
  | JsonValue[]
  | { [k: string]: JsonValue };

type JsonRpcRequest = {
  jsonrpc: '2.0';
  id: number;
  method: string;
  params?: JsonValue | JsonValue[];
};

type JsonRpcResponse =
  | { jsonrpc: '2.0'; id: number; result: any }
  | { jsonrpc: '2.0'; id: number; error: { code: number; message: string; data?: any } };

type JsonRpcNotify = {
  jsonrpc: '2.0';
  method: string;
  params?: any;
};

export type SubscriptionKind = 'newHeads' | 'pendingTxs' | string;

export interface WsClientOptions {
  /** ws(s):// endpoint; if http(s):// is passed we auto-convert to ws(s) */
  url: string;

  /** Optional token appended as ?token=... (browser-safe auth) */
  token?: string;

  /** Extra query params to append */
  query?: Record<string, string | number | boolean | undefined>;

  /** Enable/disable auto-reconnect (default: true) */
  autoReconnect?: boolean;

  /** Initial backoff (ms) (default: 300) */
  backoffBaseMs?: number;

  /** Max backoff cap (ms) (default: 5000) */
  backoffMaxMs?: number;

  /** Heartbeat ping interval (ms). If null/0, disabled. (default: 15_000) */
  heartbeatIntervalMs?: number;

  /** Idle timeout (ms). If no messages within this window, force reconnect. (default: 45_000) */
  idleTimeoutMs?: number;

  /** JSON-RPC method used to subscribe (default: "subscribe") */
  subscribeMethod?: string;

  /** JSON-RPC method used to unsubscribe (default: "unsubscribe") */
  unsubscribeMethod?: string;

  /** Server notify method name for stream items (default: "subscription") */
  notifyMethod?: string;

  /** Optional console-like logger (info/warn/error). Default uses console. */
  logger?: Pick<Console, 'info' | 'warn' | 'error'>;
}

type SubRecord = {
  kind: SubscriptionKind;
  params?: JsonValue | JsonValue[];
  handler: (data: any) => void;
  onError?: (err: Error) => void;
  /** Server-assigned subscription id (once established) */
  subId?: string;
  /** Local identity for resubscribe bookkeeping */
  localId: number;
};

type PendingCall = {
  resolve: (v: any) => void;
  reject: (e: any) => void;
  method: string;
};

function toWsUrl(url: string): string {
  if (url.startsWith('ws://') || url.startsWith('wss://')) return url;
  if (url.startsWith('http://')) return 'ws://' + url.slice('http://'.length);
  if (url.startsWith('https://')) return 'wss://' + url.slice('https://'.length);
  return url; // trust caller
}

function jittered(ms: number): number {
  // full jitter
  return Math.floor(Math.random() * ms);
}

export class WsClient {
  private opts: Required<
    Pick<
      WsClientOptions,
      | 'autoReconnect'
      | 'backoffBaseMs'
      | 'backoffMaxMs'
      | 'heartbeatIntervalMs'
      | 'idleTimeoutMs'
      | 'subscribeMethod'
      | 'unsubscribeMethod'
      | 'notifyMethod'
    >
  > & {
    url: string;
    token?: string;
    query?: Record<string, string | number | boolean | undefined>;
    logger: Pick<Console, 'info' | 'warn' | 'error'>;
  };

  private ws: WebSocket | null = null;
  private connected = false;
  private closing = false;

  private seq = (Date.now() % 1_000_000) | 0;
  private callMap = new Map<number, PendingCall>();

  private subMapByServerId = new Map<string, SubRecord>();
  private subsByLocalId = new Map<number, SubRecord>();
  private nextLocalSubId = 1;

  private sendQueue: string[] = [];

  private reconnectAttempt = 0;
  private hbTimer: any = null;
  private idleTimer: any = null;
  private lastActivity = Date.now();

  constructor(options: WsClientOptions) {
    const {
      url,
      token,
      query,
      autoReconnect = true,
      backoffBaseMs = 300,
      backoffMaxMs = 5_000,
      heartbeatIntervalMs = 15_000,
      idleTimeoutMs = 45_000,
      subscribeMethod = 'subscribe',
      unsubscribeMethod = 'unsubscribe',
      notifyMethod = 'subscription',
      logger,
    } = options;

    this.opts = {
      url,
      token,
      query,
      autoReconnect,
      backoffBaseMs,
      backoffMaxMs,
      heartbeatIntervalMs,
      idleTimeoutMs,
      subscribeMethod,
      unsubscribeMethod,
      notifyMethod,
      logger: logger ?? console,
    };
  }

  /* --------------------------------- Public -------------------------------- */

  connect(): Promise<void> {
    this.closing = false;
    return this.openSocket();
  }

  async close(): Promise<void> {
    this.closing = true;
    this.clearTimers();
    this.connected = false;
    if (this.ws && (this.ws.readyState === this.ws.OPEN || this.ws.readyState === this.ws.CONNECTING)) {
      try {
        this.ws.close(1000, 'client closing');
      } catch {}
    }
    this.ws = null;

    // reject pending calls
    for (const [id, pending] of this.callMap) {
      pending.reject(new Error('Connection closed'));
      this.callMap.delete(id);
    }
  }

  /**
   * Subscribe to a stream topic (e.g., "newHeads", "pendingTxs").
   * Returns an async unsubscribe function.
   */
  async subscribe(
    kind: SubscriptionKind,
    params: JsonValue | JsonValue[] | undefined,
    handler: (data: any) => void,
    onError?: (err: Error) => void
  ): Promise<() => Promise<void>> {
    const localId = this.nextLocalSubId++;
    const rec: SubRecord = { kind, params, handler, onError, localId };
    this.subsByLocalId.set(localId, rec);

    // If already connected, fire subscribe immediately; else it will be replayed after connect.
    if (this.connected) {
      try {
        await this.establishServerSubscription(rec);
      } catch (e: any) {
        // Keep local record so it can try again on reconnect; also surface error to caller.
        onError?.(e instanceof Error ? e : new Error(String(e)));
      }
    }

    return async () => {
      await this.unsubscribeLocal(localId);
    };
  }

  /** Convenience: subscribe to newHeads */
  subscribeNewHeads(
    handler: (head: any) => void,
    onError?: (e: Error) => void
  ): Promise<() => Promise<void>> {
    return this.subscribe('newHeads', undefined, handler, onError);
  }

  /** Convenience: subscribe to pendingTxs */
  subscribePendingTxs(
    handler: (tx: any) => void,
    onError?: (e: Error) => void
  ): Promise<() => Promise<void>> {
    return this.subscribe('pendingTxs', undefined, handler, onError);
  }

  /* ------------------------------- Internals -------------------------------- */

  private buildUrl(): string {
    const base = toWsUrl(this.opts.url.replace(/\/+$/, ''));
    const q: Record<string, string> = {};
    if (this.opts.token) q['token'] = this.opts.token;
    if (this.opts.query) {
      for (const [k, v] of Object.entries(this.opts.query)) {
        if (v === undefined) continue;
        q[k] = String(v);
      }
    }
    const hasQuery = Object.keys(q).length > 0;
    if (!hasQuery) return base;
    const sep = base.includes('?') ? '&' : '?';
    const qp = Object.entries(q)
      .map(([k, v]) => `${encodeURIComponent(k)}=${encodeURIComponent(v)}`)
      .join('&');
    return `${base}${sep}${qp}`;
  }

  private async openSocket(): Promise<void> {
    const url = this.buildUrl();
    const WS = (globalThis as any).WebSocket as typeof WebSocket | undefined;
    if (!WS) throw new Error('WebSocket is not available in this environment');

    return new Promise<void>((resolve, reject) => {
      try {
        this.ws = new WS(url);
      } catch (e) {
        return reject(e);
      }

      const onOpen = () => {
        this.connected = true;
        this.reconnectAttempt = 0;
        this.lastActivity = Date.now();
        this.opts.logger.info?.('[ws] connected');
        this.flushQueue();
        this.startTimers();
        // re-subscribe active
        this.replaySubscriptions().then(resolve).catch(reject);
      };

      const onMessage = (ev: MessageEvent<string>) => {
        this.lastActivity = Date.now();
        this.handleMessage(ev.data);
      };

      const onError = () => {
        this.opts.logger.warn?.('[ws] error event');
        // Errors are often followed by close; we rely on close for reconnect.
      };

      const onClose = (ev: CloseEvent) => {
        this.connected = false;
        this.clearTimers();
        this.opts.logger.warn?.(`[ws] closed (${ev.code}) ${ev.reason || ''}`);
        // Drop server subId map (will be re-established on reconnect)
        this.subMapByServerId.clear();

        // Reject all inflight calls
        for (const [id, p] of this.callMap) {
          p.reject(new Error('Connection closed'));
          this.callMap.delete(id);
        }

        if (!this.closing && this.opts.autoReconnect) {
          this.scheduleReconnect();
        }
      };

      this.ws.addEventListener('open', onOpen, { once: true });
      this.ws.addEventListener('message', onMessage);
      this.ws.addEventListener('error', onError);
      this.ws.addEventListener('close', onClose);
    });
  }

  private scheduleReconnect() {
    const { backoffBaseMs, backoffMaxMs } = this.opts;
    const exp = Math.min(backoffMaxMs, backoffBaseMs * 2 ** this.reconnectAttempt);
    const delay = jittered(exp);
    this.reconnectAttempt++;
    this.opts.logger.info?.(`[ws] reconnecting in ~${delay}ms`);
    setTimeout(() => {
      if (this.closing) return;
      this.openSocket().catch((e) => {
        this.opts.logger.error?.(`[ws] reconnect failed: ${String(e)}`);
        // try again
        this.scheduleReconnect();
      });
    }, delay);
  }

  private startTimers() {
    this.clearTimers();
    if (this.opts.heartbeatIntervalMs > 0) {
      this.hbTimer = setInterval(() => this.heartbeat(), this.opts.heartbeatIntervalMs);
    }
    if (this.opts.idleTimeoutMs > 0) {
      this.idleTimer = setInterval(() => {
        const idleFor = Date.now() - this.lastActivity;
        if (idleFor > this.opts.idleTimeoutMs) {
          this.opts.logger.warn?.('[ws] idle timeout — forcing reconnect');
          // Force reconnect by closing; onClose handler will schedule reconnect.
          try {
            this.ws?.close(4000, 'idle timeout');
          } catch {}
        }
      }, Math.max(2_000, Math.floor(this.opts.idleTimeoutMs / 3)));
    }
  }

  private clearTimers() {
    if (this.hbTimer) clearInterval(this.hbTimer);
    if (this.idleTimer) clearInterval(this.idleTimer);
    this.hbTimer = null;
    this.idleTimer = null;
  }

  private heartbeat() {
    // Application-level heartbeat. If the node doesn't implement "ping",
    // it's fine — errors are ignored and won't crash the connection.
    this.call('ping').catch(() => void 0);
  }

  private flushQueue() {
    if (!this.ws || this.ws.readyState !== this.ws.OPEN) return;
    for (const msg of this.sendQueue) {
      this.ws.send(msg);
    }
    this.sendQueue = [];
  }

  private sendRaw(msg: string) {
    if (!this.ws || this.ws.readyState !== this.ws.OPEN) {
      this.sendQueue.push(msg);
      return;
    }
    this.ws.send(msg);
  }

  private nextId(): number {
    this.seq = (this.seq + 1) % 9_007_199_254_740_000;
    return this.seq;
  }

  private call<T = any>(method: string, params?: JsonValue | JsonValue[]): Promise<T> {
    const id = this.nextId();
    const req: JsonRpcRequest = { jsonrpc: '2.0', id, method, ...(params !== undefined ? { params } : {}) };
    const payload = JSON.stringify(req);

    return new Promise<T>((resolve, reject) => {
      this.callMap.set(id, { resolve, reject, method });
      try {
        this.sendRaw(payload);
      } catch (e) {
        this.callMap.delete(id);
        reject(e);
      }
    });
  }

  private async establishServerSubscription(rec: SubRecord): Promise<void> {
    const paramsArr =
      rec.params === undefined
        ? [rec.kind]
        : Array.isArray(rec.params)
          ? [rec.kind, ...rec.params]
          : [rec.kind, rec.params];

    const subId: string = await this.call(this.opts.subscribeMethod, paramsArr);
    rec.subId = String(subId);
    this.subMapByServerId.set(rec.subId, rec);
  }

  private async unsubscribeLocal(localId: number): Promise<void> {
    const rec = this.subsByLocalId.get(localId);
    if (!rec) return;
    this.subsByLocalId.delete(localId);

    // If connected and we have a server sub id, send unsubscribe
    if (this.connected && rec.subId) {
      try {
        await this.call(this.opts.unsubscribeMethod, [rec.subId]);
      } catch (e) {
        // Log and continue
        this.opts.logger.warn?.(`[ws] unsubscribe failed: ${String(e)}`);
      }
      this.subMapByServerId.delete(rec.subId);
    }
  }

  private async replaySubscriptions(): Promise<void> {
    // Recreate each active local subscription on the server
    for (const rec of this.subsByLocalId.values()) {
      try {
        await this.establishServerSubscription(rec);
      } catch (e: any) {
        rec.onError?.(e instanceof Error ? e : new Error(String(e)));
      }
    }
  }

  private handleMessage(raw: string) {
    let msg: any;
    try {
      msg = JSON.parse(raw);
    } catch {
      this.opts.logger.warn?.('[ws] received non-JSON message');
      return;
    }

    // Response?
    if (msg && typeof msg.id === 'number' && msg.jsonrpc === '2.0') {
      const pending = this.callMap.get(msg.id);
      if (!pending) return;
      this.callMap.delete(msg.id);

      const res = msg as JsonRpcResponse;
      if ('result' in res) {
        pending.resolve(res.result);
      } else if ('error' in res) {
        const err = new Error(`${res.error.code}: ${res.error.message}`);
        (err as any).code = res.error.code;
        (err as any).data = res.error.data;
        pending.reject(err);
      }
      return;
    }

    // Notify?
    const note = msg as JsonRpcNotify;
    if (note && note.method === this.opts.notifyMethod && note.params) {
      // Expect shape: { subscription, result }
      const subId = String(note.params.subscription ?? '');
      if (!subId) return;
      const rec = this.subMapByServerId.get(subId);
      if (!rec) return;
      try {
        rec.handler(note.params.result);
      } catch (e) {
        this.opts.logger.error?.(`[ws] handler error: ${String(e)}`);
        rec.onError?.(e as Error);
      }
      return;
    }

    // Unknown message — just log
    this.opts.logger.warn?.('[ws] unknown message', note);
  }
}

/* ------------------------------ Factory helpers ---------------------------- */

/**
 * Create a WS client from URL and optional token/query. Does not auto-connect.
 */
export function createWs(opts: WsClientOptions): WsClient {
  return new WsClient(opts);
}

/**
 * Quick helper tailored for env-style config:
 *   const ws = wsFromEnv(import.meta.env.VITE_RPC_URL, import.meta.env.VITE_API_KEY);
 *   await ws.connect();
 */
export function wsFromEnv(url?: string, token?: string): WsClient {
  const wsUrl = url ?? inferWsUrl();
  return new WsClient({ url: wsUrl, token });
}
