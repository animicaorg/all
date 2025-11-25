/**
 * provider.ts — Animica provider utilities (detection, shim, RPC fallback)
 *
 * This module exposes:
 *  - getAnimicaProvider(): AnimicaProvider
 *  - httpRpc(): JSON-RPC helper over HTTP POST
 *  - installAnimicaShim(): optional fallback that adds window.animica
 *
 * The shim provides a minimal AIP-1193-like interface with:
 *   - request({ method, params })
 *   - on(event, cb) / removeListener(event, cb)
 *
 * Supported wallet-ish methods on the shim:
 *   - animica_requestAccounts -> string[]
 *   - animica_chainId -> number
 *   - animica_switchChain({ chainId }) -> void
 * Plus any chain/state RPC forwarded to the node via HTTP:
 *   - chain.getHead, state.getBalance, state.getNonce, etc.
 *
 * Events:
 *   - 'accountsChanged' (shim emits once on install; empty array)
 *   - 'chainChanged' (emitted on animica_switchChain)
 *   - 'newHeads' (emitted when head polling observes a change)
 */

export type Json = null | boolean | number | string | Json[] | { [k: string]: Json };

export type HeadView = {
  number: number;
  hash: string;
  timestamp?: number;
};

export interface AnimicaProvider {
  request<T = unknown>(args: { method: string; params?: unknown[] | object }): Promise<T>;
  on?(event: string, listener: (...args: any[]) => void): void;
  removeListener?(event: string, listener: (...args: any[]) => void): void;
}

declare global {
  interface Window {
    animica?: AnimicaProvider;
  }
}

/* --------------------------------- Config --------------------------------- */

const RPC_URL: string = (import.meta as any).env?.VITE_RPC_URL ?? "http://localhost:8545/rpc";
const DEFAULT_CHAIN_ID: number = Number((import.meta as any).env?.VITE_CHAIN_ID ?? "1337");

/* ------------------------------ JSON-RPC HTTP ------------------------------ */

type JsonRpcResponse<T = unknown> =
  | { jsonrpc: "2.0"; id: number | string | null; result: T }
  | { jsonrpc: "2.0"; id: number | string | null; error: { code: number; message: string; data?: unknown } };

export async function httpRpc<T = unknown>(method: string, params?: unknown, rpcUrl: string = RPC_URL): Promise<T> {
  const body = {
    jsonrpc: "2.0" as const,
    id: Math.floor(Math.random() * 1e9),
    method,
    params: params ?? [],
  };

  const res = await fetch(rpcUrl, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(body),
  });

  if (!res.ok) {
    const text = await res.text().catch(() => "");
    throw new Error(`RPC HTTP ${res.status}: ${text || res.statusText}`);
  }

  const json = (await res.json()) as JsonRpcResponse<T>;
  if ("error" in json) {
    const { code, message } = json.error;
    throw new Error(`RPC ${method} failed: ${message} (${code})`);
  }
  return json.result as T;
}

/* --------------------------------- Emitter -------------------------------- */

class TinyEmitter {
  private m = new Map<string, Set<(...a: any[]) => void>>();

  on(event: string, fn: (...a: any[]) => void) {
    let s = this.m.get(event);
    if (!s) {
      s = new Set();
      this.m.set(event, s);
    }
    s.add(fn);
  }

  removeListener(event: string, fn: (...a: any[]) => void) {
    this.m.get(event)?.delete(fn);
  }

  emit(event: string, ...args: any[]) {
    const s = this.m.get(event);
    if (!s || s.size === 0) return;
    for (const fn of Array.from(s)) {
      try {
        fn(...args);
      } catch (err) {
        // swallow to avoid breaking other listeners
        console.error(`[provider emitter] listener error for "${event}":`, err);
      }
    }
  }
}

/* ------------------------------ Shim Provider ------------------------------ */

class AnimicaHttpShim implements AnimicaProvider {
  private readonly rpcUrl: string;
  private chainId: number;
  private readonly emitter = new TinyEmitter();
  private headTimer: number | null = null;
  private lastHead: HeadView | null = null;
  private pollingMs: number;

  // In a real wallet these would be user-selected; the shim uses empty list.
  private accounts: string[] = [];

  constructor(opts?: { rpcUrl?: string; chainId?: number; headPollIntervalMs?: number }) {
    this.rpcUrl = opts?.rpcUrl ?? RPC_URL;
    this.chainId = Number(opts?.chainId ?? DEFAULT_CHAIN_ID);
    this.pollingMs = Math.max(500, Math.floor(opts?.headPollIntervalMs ?? 2500));

    // Kick off head polling lazily on first 'newHeads' subscription; or do it now:
    this.startHeadPolling();

    // Emit current state once so consumers learn about initial values (optional).
    queueMicrotask(() => {
      this.emitter.emit("accountsChanged", this.accounts.slice());
      this.emitter.emit("chainChanged", this.chainId);
    });
  }

  async request<T = unknown>(args: { method: string; params?: unknown[] | object }): Promise<T> {
    const method = args?.method;
    const params = args?.params;

    switch (method) {
      case "animica_requestAccounts":
        // Shim: return a deterministic demo account to make UI happy in dev/test.
        return ["animica1-demo-account"] as unknown as T;

      case "animica_chainId":
        return this.chainId as unknown as T;

      case "animica_switchChain": {
        const cid = normalizeSwitchChainParams(params);
        if (cid == null) throw new Error("animica_switchChain: missing/invalid chainId");
        if (Number(cid) !== this.chainId) {
          this.chainId = Number(cid);
          this.emitter.emit("chainChanged", this.chainId);
        }
        return undefined as unknown as T;
      }

      // Optional: some wallets expose subscribe/unsubscribe. The shim will no-op and rely on polling.
      case "animica_subscribe":
      case "animica_unsubscribe":
        return undefined as unknown as T;

      default:
        // Forward any other call to the node HTTP endpoint.
        return httpRpc<T>(method, params, this.rpcUrl);
    }
  }

  on(event: string, listener: (...args: any[]) => void) {
    this.emitter.on(event, listener);

    // Lazy-start for 'newHeads' if not already started.
    if (event === "newHeads" && this.headTimer == null) {
      this.startHeadPolling();
    }
  }

  removeListener(event: string, listener: (...args: any[]) => void) {
    this.emitter.removeListener(event, listener);
  }

  /* ---------------------------- Internal helpers --------------------------- */

  private startHeadPolling() {
    if (this.headTimer != null) return;
    this.headTimer = window.setInterval(async () => {
      try {
        const h = await httpRpc<HeadView>("chain.getHead", undefined, this.rpcUrl);
        // Emit only on change
        if (!this.lastHead || h.hash !== this.lastHead.hash || h.number !== this.lastHead.number) {
          this.lastHead = h;
          this.emitter.emit("newHeads", h);
        }
      } catch (err) {
        // Surface as a 'disconnect' flavor error once; avoid spamming.
        this.emitter.emit("disconnect", err);
      }
    }, this.pollingMs);
  }
}

/* ------------------------------- Public API -------------------------------- */

/**
 * Returns the injected provider if present (window.animica),
 * otherwise returns a singleton HTTP shim that implements the same surface.
 */
let _shimInstance: AnimicaHttpShim | null = null;

export function getAnimicaProvider(): AnimicaProvider {
  if (typeof window !== "undefined" && window.animica) return window.animica;
  if (!_shimInstance) _shimInstance = new AnimicaHttpShim();
  return _shimInstance;
}

/**
 * Installs a shim at window.animica if none exists.
 * Useful for apps that expect a provider unconditionally.
 */
export function installAnimicaShim(opts?: { rpcUrl?: string; chainId?: number; headPollIntervalMs?: number }) {
  if (typeof window === "undefined") return;
  if (!window.animica) {
    window.animica = new AnimicaHttpShim(opts);
    // Announce a synthetic "connect" for UX parity
    queueMicrotask(() => (window as any).animica?.on?.("connect", () => void 0));
  }
}

/* ---------------------------------- Utils ---------------------------------- */

function normalizeSwitchChainParams(params?: unknown[] | object): number | null {
  if (Array.isArray(params)) {
    const x = (params as any)[0];
    if (typeof x === "number") return x;
    if (x && typeof x === "object" && "chainId" in x) return Number((x as any).chainId);
  } else if (params && typeof params === "object") {
    const p = params as any;
    if ("chainId" in p) return Number(p.chainId);
  }
  return null;
}

/* --------------------------- Convenience helpers --------------------------- */

/**
 * Connects the provider (request accounts) if supported; returns first account or null.
 */
export async function connectWallet(p: AnimicaProvider = getAnimicaProvider()): Promise<string | null> {
  try {
    const accounts = await p.request<string[]>({ method: "animica_requestAccounts" });
    return (accounts && accounts[0]) || null;
  } catch {
    return null;
  }
}

/**
 * Reads the current chain id from the provider (or shim).
 */
export async function readChainId(p: AnimicaProvider = getAnimicaProvider()): Promise<number> {
  try {
    const id = await p.request<number>({ method: "animica_chainId" });
    return Number(id);
  } catch {
    return DEFAULT_CHAIN_ID;
  }
}

/**
 * Ensures the provider is on a specific chain id (no-op for shim).
 */
export async function ensureChainId(chainId: number, p: AnimicaProvider = getAnimicaProvider()): Promise<void> {
  try {
    const current = Number(await p.request<number>({ method: "animica_chainId" }));
    if (current !== Number(chainId)) {
      await p.request({ method: "animica_switchChain", params: [{ chainId }] });
    }
  } catch {
    /* best-effort */
  }
}

/**
 * Subscribes to new heads; returns an unsubscribe function.
 * Works for both injected providers and the shim (polling).
 */
export function onNewHeads(cb: (h: HeadView) => void, p: AnimicaProvider = getAnimicaProvider()): () => void {
  const listener = (h: HeadView) => cb(h);
  p.on?.("newHeads", listener);
  return () => p.removeListener?.("newHeads", listener);
}
