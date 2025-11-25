/**
 * Randomness / Beacon Client
 * -----------------------------------------------------------------------------
 * Read latest beacon and historical rounds; assist with commit/reveal flows.
 *
 * Design goals:
 *  - Isomorphic (Browser + Node) using global fetch (or injected)
 *  - Retries with exponential backoff + jitter on 429/5xx
 *  - Per-request timeouts
 *  - Pure helpers for local commitment/reveal preparation & verification
 *
 * Expected JSON-RPC methods exposed by the node (or a gateway):
 *   - rand_getLatest      => { round, randomness, timestamp? }
 *   - rand_getRound       => { round, status, startTime?, endTime?, beacon?, commitments?, reveals? }
 *   - rand_listRounds     => Round[] (descending or paged)
 *   - rand_getCommitments => { round, items: [{ address, commitment }] }
 *   - rand_getReveals     => { round, items: [{ address, secretHex, commitment }] }
 *   - rand_commit         => { txHash }   // optional: submit commitment (if node allows via RPC)
 *   - rand_reveal         => { txHash }   // optional: submit reveal (if node allows via RPC)
 *
 * If your deployment uses different names, wrap/extend BeaconClient#call().
 */

/* ---------------------------------- Types ----------------------------------- */

export type Json = null | boolean | number | string | Json[] | { [k: string]: Json };

export interface Beacon {
  round: number;
  randomness: string;      // hex 0x… (e.g., 32 bytes)
  timestamp?: string;      // ISO timestamp of settlement
}

export type RoundStatus = 'open' | 'reveal' | 'complete' | 'failed';

export interface RoundInfo {
  round: number;
  status: RoundStatus;
  startTime?: string;
  endTime?: string;
  beacon?: string | null;          // hex; present when complete
  commitments?: number;            // count
  reveals?: number;                // count
}

export interface CommitmentItem {
  address: string;                 // bech32 or hex
  commitment: string;              // hex 0x… (32 bytes)
}

export interface RevealItem {
  address: string;
  secretHex: string;               // 0x… used to derive commitment
  commitment: string;              // echo of on-chain commitment
}

export interface CommitResult {
  txHash?: string;
  accepted?: boolean;
}

export interface RevealResult {
  txHash?: string;
  accepted?: boolean;
}

/* ------------------------------- Client config ------------------------------ */

export interface BeaconClientOptions {
  /** Node JSON-RPC base URL (e.g., https://rpc.devnet.animica.xyz) */
  baseUrl: string;
  /** Optional headers (e.g., Authorization, X-Chain-Id) */
  headers?: Record<string, string>;
  /** Attempts for list/rounds requests. Default: 3 */
  retries?: number;
  /** Attempts for single-item requests. Default: 2 */
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
  const { createHash } = await import('crypto'); // Node fallback
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
  for (let i = 0; i < bytes.length; i++) s += bytes[i].toString(16).padStart(2, '0');
  return s;
}

export function utf8(str: string): Uint8Array {
  return new TextEncoder().encode(str);
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

export class BeaconClient {
  private url: string;
  private headers: Record<string, string>;
  private retries: number;
  private singleRetries: number;
  private timeoutMs: number;
  private backoffBaseMs: number;
  private backoffMaxMs: number;
  private _fetch: typeof fetch;
  private _id = 1;

  constructor(opts: BeaconClientOptions) {
    if (!opts.baseUrl) throw new Error('BeaconClient: baseUrl required');
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
    if (!this._fetch) throw new Error('BeaconClient: fetch is not available in this environment');
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

  /* ------------------------------- Read methods ------------------------------ */

  /** Latest settled beacon (if any) */
  getLatest(): Promise<Beacon> {
    return this.call<Beacon>('rand_getLatest', undefined, this.singleRetries);
  }

  /** Get a specific round info (status, beacon if settled) */
  getRound(round: number): Promise<RoundInfo> {
    return this.call<RoundInfo>('rand_getRound', { round }, this.singleRetries);
  }

  /**
   * List recent rounds.
   * @param from  Optional starting round (inclusive). If omitted, server chooses.
   * @param limit Max items to return (server may cap). Default: 20.
   */
  listRounds(params?: { from?: number; limit?: number }): Promise<RoundInfo[]> {
    return this.call<RoundInfo[]>('rand_listRounds', params ?? { limit: 20 }, this.retries);
  }

  /** Get commitments posted for a round (for UIs / verification) */
  getCommitments(round: number): Promise<{ round: number; items: CommitmentItem[] }> {
    return this.call('rand_getCommitments', { round }, this.retries);
  }

  /** Get reveals published for a round (for UIs / verification) */
  getReveals(round: number): Promise<{ round: number; items: RevealItem[] }> {
    return this.call('rand_getReveals', { round }, this.retries);
  }

  /* -------------------------- Commit/Reveal helpers -------------------------- */

  /**
   * Compute a domain-separated commitment for a secret.
   * commit = SHA256( "animica:rand:commit:v1" || round_le64 || addr_len || addr_utf8 || secret )
   * - round encoded as unsigned 64-bit little-endian
   * - address optional; include if commitments are per-address
   */
  async computeCommitment(
    secret: Uint8Array | string,
    round: number,
    address?: string,
  ): Promise<string> {
    const secretBytes = typeof secret === 'string'
      ? (secret.startsWith('0x') ? hexToBytes(secret) : utf8(secret))
      : secret;

    const prefix = utf8('animica:rand:commit:v1');
    const roundLe = new Uint8Array(8);
    let r = BigInt(round);
    for (let i = 0; i < 8; i++) {
      roundLe[i] = Number(r & BigInt(0xff));
      r >>= BigInt(8);
    }
    const addrBytes = address ? utf8(address) : new Uint8Array(0);
    const addrLen = new Uint8Array(2);
    const view = new DataView(addrLen.buffer);
    view.setUint16(0, addrBytes.length, true);

    const buf = concat(prefix, roundLe, addrLen, addrBytes, secretBytes);
    const digest = await sha256(buf);
    return bytesToHex(digest);
  }

  /**
   * Verify that a (round, address?, secret) pair matches a given commitment hex.
   */
  async verifyCommitment(
    commitmentHex: string,
    secret: Uint8Array | string,
    round: number,
    address?: string,
  ): Promise<boolean> {
    const c = (await this.computeCommitment(secret, round, address)).toLowerCase();
    return normalizeHex(commitmentHex) === c;
  }

  /**
   * Prepare and submit a commitment via RPC (if allowed by your node).
   * If your node requires a signed transaction, this endpoint may reject;
   * in that case, use your wallet flow and only use this for preview.
   */
  async submitCommit(params: {
    round: number;
    address: string;                       // sender (UI identity)
    secret: Uint8Array | string;           // client-chosen secret
  }): Promise<CommitResult> {
    const commitment = await this.computeCommitment(params.secret, params.round, params.address);
    return this.call<CommitResult>(
      'rand_commit',
      { round: params.round, from: params.address, commitment },
      this.singleRetries,
    );
  }

  /**
   * Submit a reveal via RPC (if allowed by your node).
   * Sends the raw secret as 0x-hex; node contracts verify against commitment.
   */
  async submitReveal(params: {
    round: number;
    address: string;
    secret: Uint8Array | string;
  }): Promise<RevealResult> {
    const secretHex = bytesToHex(
      typeof params.secret === 'string'
        ? (params.secret.startsWith('0x') ? hexToBytes(params.secret) : utf8(params.secret))
        : params.secret,
    );
    return this.call<RevealResult>(
      'rand_reveal',
      { round: params.round, from: params.address, secretHex },
      this.singleRetries,
    );
  }
}

/* ------------------------------ Env convenience ----------------------------- */

/**
 * Construct a BeaconClient from environment-like values (Vite-friendly).
 * Uses:
 *  - VITE_RPC_URL      (required)
 *  - VITE_CHAIN_ID     (optional; X-Chain-Id header if present)
 *  - VITE_RPC_KEY      (optional; Authorization: Bearer <key>)
 */
export function beaconClientFromEnv(env?: {
  VITE_RPC_URL?: string;
  VITE_CHAIN_ID?: string | number;
  VITE_RPC_KEY?: string;
}): BeaconClient {
  const e = env ?? ((typeof import.meta !== 'undefined' ? (import.meta as any).env : {}) as any);
  const baseUrl = e?.VITE_RPC_URL;
  if (!baseUrl) throw new Error('VITE_RPC_URL is required to initialize BeaconClient');

  const headers: Record<string, string> = {};
  if (e?.VITE_CHAIN_ID) headers['X-Chain-Id'] = String(e.VITE_CHAIN_ID);
  if (e?.VITE_RPC_KEY) headers['Authorization'] = `Bearer ${e.VITE_RPC_KEY}`;

  return new BeaconClient({ baseUrl, headers });
}

/* --------------------------------- Helpers ---------------------------------- */

function concat(...parts: Uint8Array[]): Uint8Array {
  const total = parts.reduce((n, p) => n + p.length, 0);
  const out = new Uint8Array(total);
  let o = 0;
  for (const p of parts) {
    out.set(p, o);
    o += p.length;
  }
  return out;
}

function normalizeHex(h: string): string {
  const s = h.toLowerCase();
  return s.startsWith('0x') ? s : '0x' + s;
}
