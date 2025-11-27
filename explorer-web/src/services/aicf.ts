/**
 * AICF RPC Helpers
 * -----------------------------------------------------------------------------
 * Small, production-ready client focused on AICF (AI / Quantum) data surfaced
 * by the node's JSON-RPC:
 *   • listProviders / getProvider
 *   • listJobs / getJob
 *   • listSettlements
 *
 * Design goals:
 *   - Isomorphic (Browser + Node) using global fetch or provided one
 *   - Robust: timeouts, retries with exponential backoff + jitter on 429/5xx
 *   - Typed responses with conservative (string) numeric fields for safety
 *
 * If your app already wraps JSON-RPC, you can adapt the class to receive that
 * wrapper instead. This module intentionally stays self-contained.
 */

import { inferChainId, inferRpcUrl } from './env';

/* ---------------------------------- Types ----------------------------------- */

export type Json = null | boolean | number | string | Json[] | { [k: string]: Json };

export interface Page<T> {
  items: T[];
  /** Opaque cursor for next page, if any */
  nextCursor?: string | null;
  /** Optional total count when backend provides it */
  total?: number;
}

/** Provider status from node: adjust as the chain evolves */
export type ProviderStatus = 'active' | 'offline' | 'unknown' | 'draining' | 'jailed';

export interface AicfProvider {
  id: string;                 // provider identifier (e.g., bech32 address or DID)
  name?: string;
  url?: string;               // optional service endpoint
  status: ProviderStatus;
  stake?: string;             // bigint as decimal string
  capacity?: number;          // nominal concurrency
  queueDepth?: number;        // current queued jobs
  latencyP50Ms?: number;
  latencyP95Ms?: number;
  successRate?: number;       // 0..1
  attrs?: Record<string, Json>;
  updatedAt?: string;         // ISO8601
}

export type JobKind = 'ai' | 'quantum';
export type JobStatus = 'queued' | 'running' | 'settled' | 'failed' | 'expired' | 'canceled';

export interface AicfJob {
  id: string;
  kind: JobKind;
  status: JobStatus;
  providerId: string;
  submitter?: string;         // address that enqueued
  model?: string;             // AI model (for kind='ai')
  promptHash?: string;        // hex
  circuitHash?: string;       // hex (for kind='quantum')
  cost?: string;              // fee/cost as decimal string
  gasUsed?: number;
  resultId?: string | null;   // artifact id / blob id for result
  txHash?: string | null;     // settlement tx (if finalized on-chain)
  submittedAt: string;        // ISO8601
  updatedAt?: string;         // ISO8601
  attrs?: Record<string, Json>;
}

export interface AicfSettlement {
  id: string;
  jobId: string;
  providerId: string;
  txHash: string;
  blockNumber?: number;
  amount?: string;            // decimal string
  paid?: boolean;
  settledAt?: string;         // ISO8601
  attrs?: Record<string, Json>;
}

/* ------------------------------ Client options ------------------------------ */

export interface AicfRpcOptions {
  /** Node JSON-RPC base URL, e.g. https://rpc.devnet.animica.xyz */
  baseUrl: string;
  /** Optional default headers (e.g., auth or chain id) */
  headers?: Record<string, string>;
  /** Attempts on GET-like (list) queries. Default: 3 */
  retries?: number;
  /** Attempts on single-item queries. Default: 2 */
  singleRetries?: number;
  /** Per-request timeout (ms). Default: 12_000 */
  timeoutMs?: number;
  /** Backoff base (ms). Default: 250 */
  backoffBaseMs?: number;
  /** Backoff max (ms). Default: 2_500 */
  backoffMaxMs?: number;
  /** Custom fetch to use (defaults to global fetch) */
  fetch?: typeof fetch;
}

/* --------------------------------- Utilities -------------------------------- */

function jitter(ms: number): number {
  return Math.floor(Math.random() * ms);
}
function sleep(ms: number): Promise<void> {
  return new Promise((r) => setTimeout(r, ms));
}

/* ------------------------------- JSON-RPC Core ------------------------------ */

type RpcId = number | string;

interface RpcReq {
  jsonrpc: '2.0';
  id: RpcId;
  method: string;
  params?: any;
}
interface RpcOk<T = any> {
  jsonrpc: '2.0';
  id: RpcId;
  result: T;
}
interface RpcErr {
  jsonrpc: '2.0';
  id: RpcId;
  error: { code: number; message: string; data?: any };
}

function isErr(x: any): x is RpcErr {
  return x && typeof x === 'object' && 'error' in x;
}

/* ---------------------------------- Client ---------------------------------- */

export class AicfRpc {
  private url: string;
  private headers: Record<string, string>;
  private retries: number;
  private singleRetries: number;
  private timeoutMs: number;
  private backoffBaseMs: number;
  private backoffMaxMs: number;
  private _fetch: typeof fetch;
  private _id = 1;

  constructor(opts: AicfRpcOptions) {
    if (!opts.baseUrl) throw new Error('AicfRpc: baseUrl required');
    this.url = opts.baseUrl.replace(/\/+$/, '');
    this.headers = {
      'Content-Type': 'application/json',
      Accept: 'application/json',
      ...(opts.headers ?? {}),
    };
    this.retries = opts.retries ?? 3;
    this.singleRetries = opts.singleRetries ?? 2;
    this.timeoutMs = opts.timeoutMs ?? 12_000;
    this.backoffBaseMs = opts.backoffBaseMs ?? 250;
    this.backoffMaxMs = opts.backoffMaxMs ?? 2_500;
    this._fetch = opts.fetch ?? (globalThis.fetch as any);
    if (!this._fetch) throw new Error('AicfRpc: fetch is not available in this environment');
  }

  private nextId(): RpcId {
    const id = this._id++;
    if (this._id > 1e9) this._id = 1;
    return id;
  }

  private async call<T>(method: string, params?: any, attempts?: number): Promise<T> {
    const payload: RpcReq = { jsonrpc: '2.0', id: this.nextId(), method, params };
    const maxAttempts = attempts ?? this.retries;

    let tryNo = 0;
    let lastErr: any;

    while (tryNo <= maxAttempts) {
      const controller = new AbortController();
      const t = setTimeout(() => controller.abort(), this.timeoutMs);

      try {
        const res = await this._fetch(this.url, {
          method: 'POST',
          headers: this.headers,
          body: JSON.stringify(payload),
          signal: controller.signal,
        });
        clearTimeout(t);

        // Retry on 429/5xx
        if (res.status === 429 || (res.status >= 500 && res.status <= 599)) {
          lastErr = new Error(`HTTP ${res.status}`);
        } else if (!res.ok) {
          const text = await res.text().catch(() => '');
          throw new Error(`HTTP ${res.status}: ${text || res.statusText}`);
        } else {
          const data = (await res.json()) as RpcOk<T> | RpcErr;
          if (isErr(data)) {
            const e = new Error(data.error?.message || 'RPC error');
            (e as any).code = data.error?.code;
            (e as any).data = data.error?.data;
            throw e;
          }
          return (data as RpcOk<T>).result;
        }
      } catch (e: any) {
        lastErr = e;
      } finally {
        clearTimeout(t);
      }

      tryNo++;
      if (tryNo > maxAttempts) break;
      const exp = Math.min(this.backoffMaxMs, this.backoffBaseMs * 2 ** (tryNo - 1));
      await sleep(exp + jitter(exp));
    }

    throw lastErr ?? new Error(`RPC ${method} failed`);
  }

  /* ----------------------------- AICF RPC Methods ---------------------------- */

  /**
   * List providers with optional filters and pagination.
   * RPC: aicf_listProviders
   */
  listProviders(params?: {
    status?: ProviderStatus;
    limit?: number;
    cursor?: string;
  }): Promise<Page<AicfProvider>> {
    return this.call<Page<AicfProvider>>('aicf_listProviders', {
      status: params?.status,
      limit: params?.limit ?? 50,
      cursor: params?.cursor ?? null,
    });
  }

  /**
   * Get a single provider by id.
   * RPC: aicf_getProvider
   */
  getProvider(id: string): Promise<AicfProvider> {
    return this.call<AicfProvider>('aicf_getProvider', { id }, this.singleRetries);
    }
  /**
   * List jobs with common filters.
   * RPC: aicf_listJobs
   */
  listJobs(params?: {
    providerId?: string;
    submitter?: string;         // address that enqueued
    status?: JobStatus;
    kind?: JobKind;
    fromTime?: string;          // ISO8601
    toTime?: string;            // ISO8601
    limit?: number;
    cursor?: string;
  }): Promise<Page<AicfJob>> {
    return this.call<Page<AicfJob>>('aicf_listJobs', {
      providerId: params?.providerId ?? null,
      submitter: params?.submitter ?? null,
      status: params?.status ?? null,
      kind: params?.kind ?? null,
      fromTime: params?.fromTime ?? null,
      toTime: params?.toTime ?? null,
      limit: params?.limit ?? 50,
      cursor: params?.cursor ?? null,
    });
  }

  /**
   * Get a single job by id.
   * RPC: aicf_getJob
   */
  getJob(id: string): Promise<AicfJob> {
    return this.call<AicfJob>('aicf_getJob', { id }, this.singleRetries);
  }

  /**
   * List settlements (on-chain payments/claims) with filters.
   * RPC: aicf_listSettlements
   */
  listSettlements(params?: {
    providerId?: string;
    jobId?: string;
    submitter?: string;         // address that paid or beneficiary
    fromBlock?: number;
    toBlock?: number;
    limit?: number;
    cursor?: string;
  }): Promise<Page<AicfSettlement>> {
    return this.call<Page<AicfSettlement>>('aicf_listSettlements', {
      providerId: params?.providerId ?? null,
      jobId: params?.jobId ?? null,
      submitter: params?.submitter ?? null,
      fromBlock: params?.fromBlock ?? null,
      toBlock: params?.toBlock ?? null,
      limit: params?.limit ?? 50,
      cursor: params?.cursor ?? null,
    });
  }
}

/* ------------------------------- Env bootstrap ------------------------------ */

/**
 * Construct an AicfRpc from environment-like values (Vite-friendly).
 * Uses:
 *  - VITE_RPC_URL      (required)
 *  - VITE_CHAIN_ID     (optional; sent as X-Chain-Id header if present)
 *  - VITE_RPC_KEY      (optional; sent as Authorization: Bearer <key>)
 */
export function aicfRpcFromEnv(env?: {
  VITE_RPC_URL?: string;
  VITE_CHAIN_ID?: string | number;
  VITE_RPC_KEY?: string;
}): AicfRpc {
  const e = env ?? ((typeof import.meta !== 'undefined' ? (import.meta as any).env : {}) as any);
  const baseUrl = inferRpcUrl(e);

  const headers: Record<string, string> = {};
  const chainId = inferChainId(e);
  if (chainId) headers['X-Chain-Id'] = chainId;
  if (e?.VITE_RPC_KEY) headers['Authorization'] = `Bearer ${e.VITE_RPC_KEY}`;

  return new AicfRpc({ baseUrl, headers });
}
