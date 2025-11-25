/**
 * Data Availability (DA) Client
 * -----------------------------------------------------------------------------
 * Resolve blob commitments to their metadata, content, and Merkle/NMT proofs.
 * Also provides local size/overhead estimators for UI hints.
 *
 * Goals:
 *  - Isomorphic (Browser + Node) using global fetch (or injected)
 *  - Robust retries with exponential backoff + jitter on 429/5xx
 *  - Timeouts for every request
 *  - Minimal, conservative typing; numeric wire values kept as strings when large
 *
 * Expected JSON-RPC methods (served by the node or gateway):
 *   - da_getMeta      => { commitment, size, chunkSize?, namespace?, height? }
 *   - da_getBlob      => { dataB64 } or { dataHex }
 *   - da_getProof     => { root, leaf, siblings, path, leafIndex?, namespace? }
 *
 * If your deployment exposes different method names, wrap this client or
 * override call() via composition.
 */

/* ---------------------------------- Types ----------------------------------- */

export type Json = null | boolean | number | string | Json[] | { [k: string]: Json };

export interface DaMeta {
  commitment: string;        // hex (0x…)
  size: number;              // original payload size in bytes
  chunkSize?: number;        // bytes per leaf (e.g., 4096)
  namespace?: string | null; // optional namespace field (hex/bech32)
  height?: number;           // tree height (optional)
}

export interface DaProof {
  root: string;              // root commitment (hex)
  leaf: string;              // leaf hash (hex)
  siblings: string[];        // sibling hashes bottom→top (hex)
  path: number[];            // 0 for left, 1 for right (same length as siblings)
  leafIndex?: number;        // optional leaf index
  namespace?: string | null; // optional namespace if NMT
}

export interface DaBlob {
  /** Raw blob bytes */
  data: Uint8Array;
  /** Original encoding as returned by RPC (for debugging/inspection) */
  _raw?: { dataB64?: string; dataHex?: string };
}

export interface SizeEstimate {
  size: number;          // payload size (bytes)
  chunkSize: number;     // chunk size used for leaves
  chunkCount: number;    // ceil(size / chunkSize)
  treeHeight: number;    // ceil(log2(chunkCount)), zero for single leaf
  proofSiblingCount: number; // expected sibling nodes in proof
  proofBytesApprox: number;  // rough estimate, assuming 32-byte node hash
}

/* ------------------------------- Client config ------------------------------ */

export interface DaClientOptions {
  /** Node JSON-RPC base URL (e.g., https://rpc.devnet.animica.xyz) */
  baseUrl: string;
  /** Optional headers (e.g., Authorization, X-Chain-Id) */
  headers?: Record<string, string>;
  /** Attempts for list/meta requests. Default: 3 */
  retries?: number;
  /** Attempts for single-item requests (blob/proof). Default: 2 */
  singleRetries?: number;
  /** Per-request timeout in ms. Default: 12_000 */
  timeoutMs?: number;
  /** Backoff base (ms). Default: 250 */
  backoffBaseMs?: number;
  /** Backoff max (ms). Default: 2_500 */
  backoffMaxMs?: number;
  /** Custom fetch implementation (defaults to global fetch) */
  fetch?: typeof fetch;
}

/* --------------------------------- Utilities -------------------------------- */

function jitter(ms: number): number {
  return Math.floor(Math.random() * ms);
}
function sleep(ms: number): Promise<void> {
  return new Promise((r) => setTimeout(r, ms));
}

function hasSubtle(): boolean {
  return !!(globalThis.crypto && (globalThis.crypto as any).subtle);
}

async function sha256(bytes: Uint8Array): Promise<Uint8Array> {
  if (hasSubtle()) {
    const h = await (globalThis.crypto as any).subtle.digest('SHA-256', bytes);
    return new Uint8Array(h);
  }
  // Node fallback
  const { createHash } = await import('crypto');
  const dig = createHash('sha256').update(bytes).digest();
  return new Uint8Array(dig.buffer, dig.byteOffset, dig.byteLength);
}

export function hexToBytes(hex: string): Uint8Array {
  const h = hex.startsWith('0x') ? hex.slice(2) : hex;
  if (h.length % 2 !== 0) throw new Error('hex string must have even length');
  const out = new Uint8Array(h.length / 2);
  for (let i = 0; i < out.length; i++) out[i] = parseInt(h.slice(i * 2, i * 2 + 2), 16);
  return out;
}

export function bytesToHex(bytes: Uint8Array): string {
  let s = '0x';
  for (let i = 0; i < bytes.length; i++) {
    const v = bytes[i].toString(16).padStart(2, '0');
    s += v;
  }
  return s;
}

function b64ToBytes(b64: string): Uint8Array {
  if (typeof atob !== 'undefined') {
    const bin = atob(b64);
    const out = new Uint8Array(bin.length);
    for (let i = 0; i < bin.length; i++) out[i] = bin.charCodeAt(i);
    return out;
  }
  // Node
  return new Uint8Array(Buffer.from(b64, 'base64'));
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

export class DaClient {
  private url: string;
  private headers: Record<string, string>;
  private retries: number;
  private singleRetries: number;
  private timeoutMs: number;
  private backoffBaseMs: number;
  private backoffMaxMs: number;
  private _fetch: typeof fetch;
  private _id = 1;

  constructor(opts: DaClientOptions) {
    if (!opts.baseUrl) throw new Error('DaClient: baseUrl required');
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
    if (!this._fetch) throw new Error('DaClient: fetch is not available in this environment');
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

  /* ------------------------------- DA methods -------------------------------- */

  /**
   * Fetch metadata for a commitment (size/chunking info).
   * RPC: da_getMeta
   */
  getMeta(commitment: string): Promise<DaMeta> {
    return this.call<DaMeta>('da_getMeta', { commitment }, this.singleRetries);
  }

  /**
   * Fetch blob content for a commitment. Supports {dataB64} or {dataHex}.
   * RPC: da_getBlob
   */
  async getBlob(commitment: string): Promise<DaBlob> {
    const res = await this.call<{ dataB64?: string; dataHex?: string }>(
      'da_getBlob',
      { commitment },
      this.singleRetries,
    );
    if (res.dataB64) {
      return { data: b64ToBytes(res.dataB64), _raw: { dataB64: res.dataB64 } };
    }
    if (res.dataHex) {
      return { data: hexToBytes(res.dataHex), _raw: { dataHex: res.dataHex } };
    }
    throw new Error('da_getBlob returned neither dataB64 nor dataHex');
  }

  /**
   * Fetch Merkle/NMT proof for a commitment.
   * RPC: da_getProof
   */
  getProof(commitment: string): Promise<DaProof> {
    return this.call<DaProof>('da_getProof', { commitment }, this.singleRetries);
  }

  /* ----------------------------- Local estimators ---------------------------- */

  /**
   * Compute a SHA-256 commitment of the *raw* bytes (conservative default).
   * Your network may define commitment differently (e.g., NMT root). This
   * helper is provided for client-side checks or preflight UIs only.
   */
  async commitmentOf(bytes: Uint8Array | ArrayBuffer | string): Promise<string> {
    const b = await normalizeBytes(bytes);
    const digest = await sha256(b);
    return bytesToHex(digest);
  }

  /**
   * Estimate tree/proof sizes for a given payload length.
   * For UIs only; on-chain commitment/parameters should come from getMeta().
   */
  estimateSizes(len: number, opts?: { chunkSize?: number; hashLen?: number }): SizeEstimate {
    const chunkSize = Math.max(1, opts?.chunkSize ?? 4096); // default 4 KiB leaves
    const hashLen = Math.max(1, opts?.hashLen ?? 32);       // 32 bytes for SHA-256 nodes

    const chunkCount = Math.max(1, Math.ceil(len / chunkSize));
    const treeHeight = chunkCount === 1 ? 0 : Math.ceil(Math.log2(chunkCount));
    const proofSiblingCount = treeHeight; // perfect binary tree
    const proofBytesApprox = proofSiblingCount * hashLen;

    return {
      size: len,
      chunkSize,
      chunkCount,
      treeHeight,
      proofSiblingCount,
      proofBytesApprox,
    };
  }
}

/* ------------------------------ Env convenience ----------------------------- */

/**
 * Construct a DaClient from environment-like values (Vite-friendly).
 * Uses:
 *  - VITE_RPC_URL      (required)
 *  - VITE_CHAIN_ID     (optional; sent as X-Chain-Id header if present)
 *  - VITE_RPC_KEY      (optional; sent as Authorization: Bearer <key>)
 */
export function daClientFromEnv(env?: {
  VITE_RPC_URL?: string;
  VITE_CHAIN_ID?: string | number;
  VITE_RPC_KEY?: string;
}): DaClient {
  const e = env ?? ((typeof import.meta !== 'undefined' ? (import.meta as any).env : {}) as any);
  const baseUrl = e?.VITE_RPC_URL;
  if (!baseUrl) throw new Error('VITE_RPC_URL is required to initialize DaClient');

  const headers: Record<string, string> = {};
  if (e?.VITE_CHAIN_ID) headers['X-Chain-Id'] = String(e.VITE_CHAIN_ID);
  if (e?.VITE_RPC_KEY) headers['Authorization'] = `Bearer ${e.VITE_RPC_KEY}`;

  return new DaClient({ baseUrl, headers });
}

/* --------------------------------- Helpers ---------------------------------- */

async function normalizeBytes(x: Uint8Array | ArrayBuffer | string): Promise<Uint8Array> {
  if (x instanceof Uint8Array) return x;
  if (typeof x === 'string') {
    // As UTF-8; if input is hex (0x...), treat as hex
    if (x.startsWith('0x') || /^[0-9a-fA-F]+$/.test(x)) {
      return hexToBytes(x);
    }
    return new TextEncoder().encode(x);
  }
  if (x instanceof ArrayBuffer) return new Uint8Array(x);
  throw new Error('Unsupported byte-like input');
}
