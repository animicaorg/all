/**
 * WS subscriptions for:
 *  - newHeads
 *  - pendingTxs
 *
 * Features:
 *  • JSON-RPC over WebSocket with auto-reconnect & exponential backoff.
 *  • Tries multiple method name variants for broader node compatibility.
 *  • Re-subscribes after reconnect.
 *  • Local listener registry (callbacks) + chrome.runtime message fanout.
 *
 * This module is used by the background service worker. UI/content/provider
 * contexts receive broadcast events via chrome.runtime.onMessage.
 */

import type { Network } from "./networks";

/* ----------------------------- types ------------------------------ */

export type Hex = `0x${string}`;

export interface HeadSummary {
  number: number;        // height
  hash: Hex;
  parentHash?: Hex;
  timestamp?: number;    // seconds
}

export interface PendingTx {
  hash: Hex;
  from?: string;
  to?: string;
  value?: string;
}

type Listener<T> = (item: T) => void;

/* ----------------------------- constants ------------------------------ */

// Subscribe variants (preferred → fallback)
const SUBSCRIBE_METHODS = ["omni_subscribe", "animica_subscribe", "subscribe"];
const NOTIFY_METHODS = ["omni_subscription", "animica_subscription", "eth_subscription"]; // accept all

// Topic names (node-dependent; we send these as the 1st param to *subscribe)
const TOPIC_NEW_HEADS = ["newHeads", "new_heads"];
const TOPIC_PENDING_TXS = ["pendingTxs", "pending_transactions", "newPendingTransactions"];

// Unsubscribe variants
const UNSUBSCRIBE_METHODS = ["omni_unsubscribe", "animica_unsubscribe", "unsubscribe"];

// Backoff (ms)
const BACKOFF_MIN = 500;
const BACKOFF_MAX = 30_000;

/* ----------------------------- helpers ------------------------------ */

function pick<T>(list: readonly T[]): T {
  return list[0];
}

function isHex(x: unknown): x is Hex {
  return typeof x === "string" && /^0x[0-9a-fA-F]*$/.test(x);
}

/* ----------------------------- WS JSON-RPC client ------------------------------ */

class WsRpc {
  private url: string;
  private ws: WebSocket | null = null;
  private nextId = 1;
  private inflight = new Map<number, { resolve: (v: any)=>void; reject: (e:any)=>void }>();
  private subs = new Map<
    string, // subscription id
    { topic: string; handler: (payload: any) => void }
  >();
  private desiredSubs: { topicVariants: string[]; handler: (payload:any)=>void }[] = [];
  private backoff = BACKOFF_MIN;
  private closedByUser = false;

  constructor(url: string) {
    this.url = url;
  }

  connect(): void {
    this.closedByUser = false;
    if (this.ws && (this.ws.readyState === WebSocket.OPEN || this.ws.readyState === WebSocket.CONNECTING)) {
      return;
    }
    try {
      this.ws = new WebSocket(this.url);
    } catch (e) {
      this.scheduleReconnect();
      return;
    }

    this.ws.onopen = () => {
      this.backoff = BACKOFF_MIN;
      // Re-subscribe desired topics on open
      for (const d of this.desiredSubs) {
        this._subscribeWithVariants(d.topicVariants, d.handler).catch(() => {/* will retry next reconnect */});
      }
    };

    this.ws.onmessage = (ev) => this.onMessage(ev);

    this.ws.onerror = () => {
      // errors are followed by close; we rely on onclose to reconnect
    };

    this.ws.onclose = () => {
      // Reject all inflight queries
      for (const [id, p] of this.inflight) {
        p.reject(new Error("WS closed"));
      }
      this.inflight.clear();
      this.subs.clear();

      if (!this.closedByUser) this.scheduleReconnect();
    };
  }

  close(): void {
    this.closedByUser = true;
    try { this.ws?.close(); } catch {}
    this.ws = null;
  }

  private scheduleReconnect(): void {
    const wait = this.backoff;
    this.backoff = Math.min(this.backoff * 2, BACKOFF_MAX);
    setTimeout(() => this.connect(), wait);
  }

  private send(obj: any): void {
    const data = JSON.stringify(obj);
    if (!this.ws || this.ws.readyState !== WebSocket.OPEN) throw new Error("WS not open");
    this.ws.send(data);
  }

  call<T = unknown>(method: string, params: any[] = []): Promise<T> {
    const id = this.nextId++;
    const payload = { jsonrpc: "2.0", id, method, params };
    return new Promise<T>((resolve, reject) => {
      this.inflight.set(id, { resolve, reject });
      try {
        this.send(payload);
      } catch (e) {
        this.inflight.delete(id);
        reject(e);
      }
    });
  }

  async subscribe(topicVariants: string[], handler: (payload: any) => void): Promise<void> {
    // Track as a desired subscription so we can re-establish after reconnects
    this.desiredSubs.push({ topicVariants, handler });
    await this._subscribeWithVariants(topicVariants, handler);
  }

  private async _subscribeWithVariants(topicVariants: string[], handler: (payload:any)=>void): Promise<void> {
    // Try subscribe methods × topics
    let lastErr: unknown = undefined;
    for (const m of SUBSCRIBE_METHODS) {
      for (const topic of topicVariants) {
        try {
          const subId = await this.call<string>(m, [topic]);
          if (!subId || typeof subId !== "string") throw new Error("Bad subscription id");
          this.subs.set(subId, { topic, handler });
          return;
        } catch (e) {
          lastErr = e;
          // try next combo
        }
      }
    }
    throw lastErr ?? new Error("subscribe failed");
  }

  async unsubscribeAll(): Promise<void> {
    const ids = Array.from(this.subs.keys());
    this.subs.clear();
    // best-effort
    for (const id of ids) {
      for (const m of UNSUBSCRIBE_METHODS) {
        try {
          // eslint-disable-next-line no-await-in-loop
          await this.call(m, [id]);
          break;
        } catch {/* try next */}
      }
    }
  }

  private onMessage(ev: MessageEvent): void {
    let msg: any;
    try {
      msg = JSON.parse(String(ev.data));
    } catch {
      return;
    }

    // Response to a call
    if (typeof msg?.id === "number") {
      const pending = this.inflight.get(msg.id);
      if (!pending) return;
      this.inflight.delete(msg.id);
      if ("result" in msg) {
        pending.resolve(msg.result);
      } else if ("error" in msg) {
        pending.reject(Object.assign(new Error(msg.error?.message ?? "RPC error"), { code: msg.error?.code, data: msg.error?.data }));
      } else {
        pending.reject(new Error("Malformed RPC response"));
      }
      return;
    }

    // Subscription notification
    if (typeof msg?.method === "string" && NOTIFY_METHODS.includes(msg.method) && msg?.params) {
      const subId = msg.params.subscription;
      const payload = msg.params.result;
      const sub = this.subs.get(subId);
      if (sub) {
        try {
          sub.handler(payload);
        } catch {
          // ignore handler errors
        }
      }
    }
  }
}

/* ----------------------------- Manager (singleton) ------------------------------ */

class SubscriptionsManager {
  private ws: WsRpc | null = null;
  private net: Network | null = null;

  private headListeners = new Set<Listener<HeadSummary>>();
  private pendingListeners = new Set<Listener<PendingTx>>();

  ensure(net: Network): void {
    if (this.net && this.net.wsUrl === net.wsUrl) {
      // already connected to this network (best-effort)
      if (!this.ws) this.ws = new WsRpc(net.wsUrl);
      this.ws?.connect();
      return;
    }
    // Switch network
    this.teardown();
    this.net = net;
    this.ws = new WsRpc(net.wsUrl);
    this.ws.connect();

    // Attach subs
    // newHeads
    this.ws
      .subscribe(TOPIC_NEW_HEADS, (payload) => {
        const head = normalizeHead(payload);
        if (!head) return;
        this.emitHead(head);
      })
      .catch(() => {/* will retry after reconnect via desiredSubs */});

    // pendingTxs
    this.ws
      .subscribe(TOPIC_PENDING_TXS, (payload) => {
        const tx = normalizePending(payload);
        if (!tx) return;
        this.emitPending(tx);
      })
      .catch(() => {/* same as above */});
  }

  teardown(): void {
    try { this.ws?.unsubscribeAll(); } catch {}
    try { this.ws?.close(); } catch {}
    this.ws = null;
    this.net = null;
  }

  /* Heads */
  onNewHead(cb: Listener<HeadSummary>): () => void {
    this.headListeners.add(cb);
    return () => this.headListeners.delete(cb);
  }

  /* Pending txs */
  onPendingTx(cb: Listener<PendingTx>): () => void {
    this.pendingListeners.add(cb);
    return () => this.pendingListeners.delete(cb);
  }

  private emitHead(head: HeadSummary): void {
    // Local listeners
    for (const l of this.headListeners) {
      try { l(head); } catch {}
    }
    // Fanout to other extension contexts
    // eslint-disable-next-line @typescript-eslint/ban-ts-comment
    // @ts-ignore
    if (chrome?.runtime?.sendMessage) {
      // best-effort; MV3 SW may not have any listeners at times
      chrome.runtime.sendMessage({ __animica: true, type: "ws:newHead", payload: head });
    }
  }

  private emitPending(tx: PendingTx): void {
    for (const l of this.pendingListeners) {
      try { l(tx); } catch {}
    }
    // eslint-disable-next-line @typescript-eslint/ban-ts-comment
    // @ts-ignore
    if (chrome?.runtime?.sendMessage) {
      chrome.runtime.sendMessage({ __animica: true, type: "ws:pendingTx", payload: tx });
    }
  }
}

/* ----------------------------- normalization ------------------------------ */

function normalizeHead(x: any): HeadSummary | null {
  if (!x) return null;
  // common shapes: { number: "0x..", hash: "0x..", parentHash, timestamp }
  let number: number | null = null;
  if (typeof x.number === "number") number = x.number;
  else if (isHex(x.number)) {
    try { number = parseInt(x.number, 16); } catch { number = null; }
  }
  const hash = x.hash;
  if (number === null || !isHex(hash)) return null;
  let ts: number | undefined = undefined;
  if (typeof x.timestamp === "number") ts = x.timestamp;
  else if (isHex(x.timestamp)) {
    try { ts = parseInt(x.timestamp, 16); } catch {/* ignore */}
  }
  const parentHash = isHex(x.parentHash) ? x.parentHash : undefined;
  return { number, hash, parentHash, timestamp: ts };
}

function normalizePending(x: any): PendingTx | null {
  if (!x) return null;
  // common shapes: { hash } or the full tx object; we just require hash
  const hash = x.hash;
  if (!isHex(hash)) return null;
  return {
    hash,
    from: typeof x.from === "string" ? x.from : undefined,
    to: typeof x.to === "string" ? x.to : undefined,
    value: typeof x.value === "string" ? x.value : undefined,
  };
}

/* ----------------------------- public API ------------------------------ */

const _manager = new SubscriptionsManager();

/**
 * Start WS subscriptions (or switch to a new network).
 * Subsequent subscribe* calls will receive events.
 */
export function startSubscriptions(net: Network): void {
  _manager.ensure(net);
}

/** Stop all WS connections and listeners. */
export function stopSubscriptions(): void {
  _manager.teardown();
}

/** Register a callback for newHeads. Returns an unsubscribe fn. */
export function subscribeNewHeads(cb: Listener<HeadSummary>): () => void {
  return _manager.onNewHead(cb);
}

/** Register a callback for pendingTxs. Returns an unsubscribe fn. */
export function subscribePendingTxs(cb: Listener<PendingTx>): () => void {
  return _manager.onPendingTx(cb);
}

/* Convenience fanout: UI/content can also listen to chrome.runtime.onMessage:
chrome.runtime.onMessage.addListener((msg) => {
  if (msg?.__animica && msg.type === "ws:newHead") { ... }
  if (msg?.__animica && msg.type === "ws:pendingTx") { ... }
});
*/

export default {
  startSubscriptions,
  stopSubscriptions,
  subscribeNewHeads,
  subscribePendingTxs,
};
