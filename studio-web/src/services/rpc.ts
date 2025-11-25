/**
 * Thin wrapper over @animica/sdk RPC for studio-web.
 *
 * - Centralizes HTTP + WS client creation
 * - Exposes a small, typed surface used across the app
 * - Reads defaults from VITE_* envs but allows per-call overrides
 */

import type { Tx, Receipt, Block, Head } from '@animica/sdk/types/core';
import { JsonRpcHttpClient } from '@animica/sdk/rpc/http';
import { JsonRpcWsClient } from '@animica/sdk/rpc/ws';

export type RpcConfig = {
  /** HTTP JSON-RPC endpoint (e.g., http://localhost:8545/rpc) */
  url: string;
  /** Chain id expected by the studio (used for guardrails) */
  chainId: number;
  /** Optional WS endpoint (e.g., ws://localhost:8545/ws) for subscriptions */
  wsUrl?: string;
  /** Optional extra headers for HTTP calls */
  headers?: Record<string, string>;
};

export type PartialRpcConfig = Partial<RpcConfig>;

function envNumber(name: string, fallback?: number): number | undefined {
  const v = (import.meta as any)?.env?.[name];
  if (v == null) return fallback;
  const n = Number(v);
  return Number.isFinite(n) ? n : fallback;
}

function envString(name: string, fallback?: string): string | undefined {
  const v = (import.meta as any)?.env?.[name];
  return v ?? fallback;
}

/** Default config from env with safe fallbacks for dev */
export function getDefaultRpcConfig(overrides: PartialRpcConfig = {}): RpcConfig {
  const url =
    overrides.url ??
    envString('VITE_RPC_URL') ??
    'http://127.0.0.1:8545/rpc';
  const chainId =
    overrides.chainId ??
    envNumber('VITE_CHAIN_ID') ??
    1337;
  const wsUrl =
    overrides.wsUrl ??
    envString('VITE_WS_URL') ??
    // heuristics: turn http(s)://host:port/rpc -> ws(s)://host:port/ws
    (url.startsWith('https://')
      ? url.replace(/^https:/, 'wss:').replace(/\/rpc\/?$/, '/ws')
      : url.replace(/^http:/, 'ws:').replace(/\/rpc\/?$/, '/ws'));

  return {
    url,
    chainId,
    wsUrl,
    headers: overrides.headers,
  };
}

/** Minimal surface the app needs. Extend as features land. */
export interface Rpc {
  /** Low-level call for anything not yet wrapped. */
  request<T = unknown>(method: string, params?: unknown): Promise<T>;

  // -------- Chain / blocks --------
  getChainId(): Promise<number>;
  getParams(): Promise<Record<string, unknown>>;
  getHead(): Promise<Head>;
  getBlockByNumber(n: number | 'latest', opts?: { includeTx?: boolean; includeReceipts?: boolean }): Promise<Block | null>;
  getBlockByHash(h: string, opts?: { includeTx?: boolean; includeReceipts?: boolean }): Promise<Block | null>;

  // -------- State --------
  getBalance(address: string, tag?: number | 'latest'): Promise<string>; // hex string (wei-like)
  getNonce(address: string, tag?: number | 'latest'): Promise<number>;

  // -------- Tx --------
  sendRawTransaction(raw: string | Uint8Array): Promise<string>; // returns tx hash
  getTransactionByHash(hash: string): Promise<Tx | null>;
  getTransactionReceipt(hash: string): Promise<Receipt | null>;

  // -------- Subscriptions (WS) --------
  subscribeNewHeads(onHead: (h: Head) => void): Promise<() => void>;

  // -------- Lifecycle --------
  close(): void;
}

/** Implementation */
export class RpcClient implements Rpc {
  private http: JsonRpcHttpClient;
  private ws?: JsonRpcWsClient;
  private cfg: RpcConfig;

  constructor(cfg: PartialRpcConfig = {}) {
    this.cfg = getDefaultRpcConfig(cfg);
    this.http = new JsonRpcHttpClient(this.cfg.url, { headers: this.cfg.headers });
  }

  private ensureWs(): JsonRpcWsClient {
    if (!this.ws) {
      const url = this.cfg.wsUrl;
      if (!url) throw new Error('WS URL not configured');
      this.ws = new JsonRpcWsClient(url);
    }
    return this.ws;
  }

  /* ----------------------------- low-level call ----------------------------- */

  async request<T = unknown>(method: string, params?: unknown): Promise<T> {
    return this.http.request<T>(method, params ?? []);
  }

  /* --------------------------------- chain --------------------------------- */

  async getChainId(): Promise<number> {
    const id = await this.request<number>('chain.getChainId');
    return id >>> 0;
  }

  async getParams(): Promise<Record<string, unknown>> {
    return this.request('chain.getParams');
  }

  async getHead(): Promise<Head> {
    return this.request<Head>('chain.getHead');
  }

  async getBlockByNumber(
    n: number | 'latest',
    opts: { includeTx?: boolean; includeReceipts?: boolean } = {}
  ): Promise<Block | null> {
    const { includeTx = true, includeReceipts = false } = opts;
    return this.request<Block | null>('chain.getBlockByNumber', [n, includeTx, includeReceipts]);
  }

  async getBlockByHash(
    h: string,
    opts: { includeTx?: boolean; includeReceipts?: boolean } = {}
  ): Promise<Block | null> {
    const { includeTx = true, includeReceipts = false } = opts;
    return this.request<Block | null>('chain.getBlockByHash', [h, includeTx, includeReceipts]);
  }

  /* --------------------------------- state --------------------------------- */

  async getBalance(address: string, tag: number | 'latest' = 'latest'): Promise<string> {
    return this.request<string>('state.getBalance', [address, tag]);
  }

  async getNonce(address: string, tag: number | 'latest' = 'latest'): Promise<number> {
    return this.request<number>('state.getNonce', [address, tag]);
  }

  /* ----------------------------------- tx ---------------------------------- */

  async sendRawTransaction(raw: string | Uint8Array): Promise<string> {
    const hex = typeof raw === 'string' ? to0xHex(raw) : '0x' + bufToHex(raw);
    return this.request<string>('tx.sendRawTransaction', [hex]);
  }

  async getTransactionByHash(hash: string): Promise<Tx | null> {
    return this.request<Tx | null>('tx.getTransactionByHash', [hash]);
  }

  async getTransactionReceipt(hash: string): Promise<Receipt | null> {
    return this.request<Receipt | null>('tx.getTransactionReceipt', [hash]);
  }

  /* ------------------------------ subscriptions ---------------------------- */

  async subscribeNewHeads(onHead: (h: Head) => void): Promise<() => void> {
    const ws = this.ensureWs();
    const unsub = await ws.subscribe('newHeads', {}, onHead);
    return unsub;
  }

  /* -------------------------------- lifecycle ------------------------------ */

  close(): void {
    try {
      this.ws?.close();
    } catch { /* noop */ }
    this.ws = undefined;
  }
}

/* --------------------------------- helpers --------------------------------- */

function to0xHex(x: string): string {
  const s = x.trim();
  if (s.startsWith('0x') || s.startsWith('0X')) return '0x' + s.slice(2);
  // allow raw hex without 0x
  if (/^[0-9a-fA-F]+$/.test(s)) return '0x' + s;
  throw new Error('Expected hex string for raw transaction');
}

function bufToHex(buf: Uint8Array): string {
  let out = '';
  for (let i = 0; i < buf.length; i++) {
    const b = buf[i]!.toString(16).padStart(2, '0');
    out += b;
  }
  return out;
}

/* ------------------------------- singleton-ish ------------------------------ */

/** A shared, lazily-initialized RPC instance for app-wide use. */
let _defaultRpc: RpcClient | null = null;

export function getRpc(overrides?: PartialRpcConfig): RpcClient {
  if (overrides) {
    // caller wants a specific config — create a fresh client
    return new RpcClient(overrides);
  }
  if (!_defaultRpc) _defaultRpc = new RpcClient();
  return _defaultRpc;
}

export default getRpc;
