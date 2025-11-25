/**
 * Animica Explorer — Optional REST client
 * -----------------------------------------------------------------------------
 * A thin, production-ready wrapper for the node's lightweight explorer API
 * (omni/explorer/api.py). All methods are safe for browser and Node runtimes,
 * include retries with exponential backoff + jitter, timeouts, and flexible
 * auth (header or query).
 *
 * Endpoints are intentionally generic and may be adapted by configuring the
 * baseUrl and (optionally) the path prefix if your deployment differs.
 */

export type JsonValue =
  | null
  | boolean
  | number
  | string
  | JsonValue[]
  | { [k: string]: JsonValue };

/* ----------------------------------- Types ----------------------------------- */

export interface Pagination<T> {
  items: T[];
  next?: string | null;
  prev?: string | null;
  total?: number;
}

export interface NetworkStats {
  chainId: string | number;
  headHeight: number;
  headHash: string;
  mempoolSize?: number;
  txThroughput?: number;
  time?: string;
}

export interface BlockLite {
  height: number;
  hash: string;
  parentHash?: string;
  time: string; // ISO8601
  txs: number;
  gasUsed?: string;
  gasLimit?: string;
  proposer?: string;
  daRoot?: string;
  poies?: {
    gamma?: number;
    fairness?: number;
    mix?: number;
  };
}

export interface TxLite {
  hash: string;
  from: string;
  to?: string | null;
  value: string; // hex or decimal string
  fee: string;   // hex or decimal string
  nonce: number;
  status?: 'pending' | 'executed' | 'failed';
  blockHash?: string | null;
  blockHeight?: number | null;
  index?: number | null;
  timestamp?: string; // ISO8601
}

export interface ReceiptLite {
  txHash: string;
  status: boolean;
  gasUsed: number;
  logs: Array<{
    address: string;
    topics: string[];
    data: string;
  }>;
  contractAddress?: string | null;
  returnData?: string | null;
}

export interface TxDetail extends TxLite {
  input?: string;
  receipt?: ReceiptLite;
}

export interface AddressSummary {
  address: string;
  balance: string; // decimal or hex
  nonce: number;
  codeHash?: string | null;
  isContract: boolean;
  txCount?: number;
}

export interface ContractMeta {
  address: string;
  codeHash: string;
  verified: boolean;
  name?: string;
  compiler?: string;
  abiId?: string;
  sourceId?: string;
  verifiedAt?: string;
}

export interface AICFJob {
  id: string;
  kind: 'ai' | 'quantum';
  status: 'queued' | 'running' | 'settled' | 'failed';
  createdAt: string;
  settledAt?: string;
  cost?: number; // in network-native units
  provider?: string;
  txHash?: string;
}

export interface DABlob {
  id: string; // content-addressed id or hash
  size: number;
  commitment: string; // e.g., NMT root / hash
  postedAt: string;
  includedIn?: string | null; // block hash/height
}

export interface BeaconRound {
  round: number;
  randomness: string; // hex
  time: string; // ISO8601
}

export interface PeerInfo {
  id: string;
  addr: string;
  latencyMs: number;
  agent?: string;
  height?: number;
}

export interface PoIESMetrics {
  gamma: number;
  fairness: number;
  mix?: number;
  window: number;
}

/* ---------------------------------- Options ---------------------------------- */

export interface ExplorerApiOptions {
  /** Base URL to the explorer REST (e.g., https://node.example.com/explorer) */
  baseUrl: string;

  /** Optional prefix for API paths (default: '/api') */
  apiPrefix?: string;

  /** API key for deployments that require it */
  apiKey?: string;

  /** Auth placement (default: 'header' -> Authorization: Bearer <key>) */
  authScheme?: 'header' | 'query';

  /** Additional static headers */
  headers?: Record<string, string>;

  /** Max retries for GET (default: 3) */
  retries?: number;

  /** Base backoff in ms (default: 250) */
  backoffBaseMs?: number;

  /** Backoff cap in ms (default: 2500) */
  backoffMaxMs?: number;

  /** Request timeout in ms (default: 10_000) */
  timeoutMs?: number;

  /** Custom fetch implementation (defaults to global fetch) */
  fetch?: typeof fetch;
}

/* --------------------------------- Utilities --------------------------------- */

function trimSlash(s: string): string {
  return s.replace(/\/+$/, '');
}
function joinUrl(base: string, path: string): string {
  if (!path.startsWith('/')) path = '/' + path;
  return trimSlash(base) + path;
}
function jitter(ms: number): number {
  return Math.floor(Math.random() * ms);
}
function sleep(ms: number): Promise<void> {
  return new Promise((r) => setTimeout(r, ms));
}

function toQuery(params?: Record<string, string | number | boolean | undefined | null>): string {
  if (!params) return '';
  const ent = Object.entries(params).filter(([, v]) => v !== undefined && v !== null);
  if (!ent.length) return '';
  return (
    '?' +
    ent
      .map(([k, v]) => `${encodeURIComponent(k)}=${encodeURIComponent(String(v))}`)
      .join('&')
  );
}

/* --------------------------------- Client ----------------------------------- */

export class ExplorerApi {
  private base: string;
  private prefix: string;
  private key?: string;
  private authScheme: 'header' | 'query';
  private headers: Record<string, string>;
  private retries: number;
  private backoffBaseMs: number;
  private backoffMaxMs: number;
  private timeoutMs: number;
  private _fetch: typeof fetch;

  constructor(opts: ExplorerApiOptions) {
    this.base = trimSlash(opts.baseUrl);
    this.prefix = opts.apiPrefix ?? '/api';
    this.key = opts.apiKey;
    this.authScheme = opts.authScheme ?? 'header';
    this.headers = {
      'Accept': 'application/json',
      ...(opts.headers ?? {}),
    };
    this.retries = opts.retries ?? 3;
    this.backoffBaseMs = opts.backoffBaseMs ?? 250;
    this.backoffMaxMs = opts.backoffMaxMs ?? 2500;
    this.timeoutMs = opts.timeoutMs ?? 10_000;
    this._fetch = opts.fetch ?? (globalThis.fetch as any);
    if (!this._fetch) throw new Error('fetch is not available in this environment');
  }

  /* ------------------------------ Core request ------------------------------ */

  private async get<T>(
    path: string,
    query?: Record<string, string | number | boolean | undefined | null>
  ): Promise<T> {
    const url =
      this.authScheme === 'query' && this.key
        ? joinUrl(this.base, this.prefix + path) +
          toQuery({ ...(query ?? {}), key: this.key })
        : joinUrl(this.base, this.prefix + path) + toQuery(query ?? {});

    const headers =
      this.authScheme === 'header' && this.key
        ? { ...this.headers, Authorization: `Bearer ${this.key}` }
        : { ...this.headers };

    let attempt = 0;
    let lastErr: any;

    while (attempt <= this.retries) {
      const controller = new AbortController();
      const t = setTimeout(() => controller.abort(), this.timeoutMs);

      try {
        const res = await this._fetch(url, {
          method: 'GET',
          headers,
          signal: controller.signal,
        });
        clearTimeout(t);

        // Retry on 429 / 5xx
        if (res.status === 429 || (res.status >= 500 && res.status <= 599)) {
          lastErr = new Error(`HTTP ${res.status}`);
          // fallthrough to retry
        } else if (!res.ok) {
          const text = await res.text().catch(() => '');
          throw new Error(`HTTP ${res.status}: ${text || res.statusText}`);
        } else {
          // Parse JSON strictly
          const data = (await res.json()) as T;
          return data;
        }
      } catch (e: any) {
        lastErr = e;
        // If abort or network, we retry (unless attempts exhausted)
      } finally {
        clearTimeout(t);
      }

      attempt++;
      if (attempt > this.retries) break;
      const exp = Math.min(this.backoffMaxMs, this.backoffBaseMs * 2 ** (attempt - 1));
      await sleep(exp + jitter(exp));
    }

    throw lastErr ?? new Error('Request failed');
  }

  /* --------------------------------- Stats ---------------------------------- */

  /** GET /api/stats */
  getStats(): Promise<NetworkStats> {
    return this.get('/stats');
  }

  /* -------------------------------- Blocks ---------------------------------- */

  /** GET /api/blocks?offset&limit */
  listBlocks(params?: { offset?: number; limit?: number }): Promise<Pagination<BlockLite>> {
    return this.get('/blocks', params);
  }

  /** GET /api/blocks/{heightOrHash} */
  getBlock(heightOrHash: number | string): Promise<BlockLite & { txsList?: TxLite[] }> {
    return this.get(`/blocks/${encodeURIComponent(String(heightOrHash))}`);
  }

  /** GET /api/blocks/{heightOrHash}/txs?offset&limit */
  getBlockTxs(
    heightOrHash: number | string,
    params?: { offset?: number; limit?: number }
  ): Promise<Pagination<TxLite>> {
    return this.get(`/blocks/${encodeURIComponent(String(heightOrHash))}/txs`, params);
  }

  /* ------------------------------ Transactions ------------------------------ */

  /** GET /api/tx/{hash} */
  getTx(hash: string): Promise<TxDetail> {
    return this.get(`/tx/${encodeURIComponent(hash)}`);
  }

  /** GET /api/txs?... */
  searchTxs(params?: {
    address?: string;
    to?: string;
    from?: string;
    status?: 'pending' | 'executed' | 'failed';
    afterHeight?: number;
    beforeHeight?: number;
    offset?: number;
    limit?: number;
  }): Promise<Pagination<TxLite>> {
    return this.get('/txs', params);
  }

  /* -------------------------------- Addresses -------------------------------- */

  /** GET /api/address/{addr} */
  getAddress(addr: string): Promise<AddressSummary> {
    return this.get(`/address/${encodeURIComponent(addr)}`);
  }

  /** GET /api/address/{addr}/txs?offset&limit */
  getAddressTxs(
    addr: string,
    params?: { offset?: number; limit?: number }
  ): Promise<Pagination<TxLite>> {
    return this.get(`/address/${encodeURIComponent(addr)}/txs`, params);
  }

  /** GET /api/address/{addr}/contracts?offset&limit */
  getAddressContracts(
    addr: string,
    params?: { offset?: number; limit?: number }
  ): Promise<Pagination<ContractMeta>> {
    return this.get(`/address/${encodeURIComponent(addr)}/contracts`, params);
  }

  /* -------------------------------- Contracts -------------------------------- */

  /** GET /api/contracts?verified&search&offset&limit */
  listContracts(params?: {
    verified?: boolean;
    search?: string;
    offset?: number;
    limit?: number;
  }): Promise<Pagination<ContractMeta>> {
    return this.get('/contracts', params);
  }

  /* ---------------------------------- AICF ----------------------------------- */

  /** GET /api/aicf/jobs?status&provider&offset&limit */
  listAICFJobs(params?: {
    status?: 'queued' | 'running' | 'settled' | 'failed';
    provider?: string;
    offset?: number;
    limit?: number;
  }): Promise<Pagination<AICFJob>> {
    return this.get('/aicf/jobs', params);
  }

  /* ----------------------------------- DA ------------------------------------ */

  /** GET /api/da/blobs?offset&limit */
  listDABlobs(params?: { offset?: number; limit?: number }): Promise<Pagination<DABlob>> {
    return this.get('/da/blobs', params);
  }

  /** GET /api/da/blobs/{id} */
  getDABlob(id: string): Promise<DABlob & { dataUrl?: string }> {
    return this.get(`/da/blobs/${encodeURIComponent(id)}`);
  }

  /* --------------------------------- Beacon ---------------------------------- */

  /** GET /api/beacon/rounds?offset&limit */
  listBeaconRounds(params?: { offset?: number; limit?: number }): Promise<Pagination<BeaconRound>> {
    return this.get('/beacon/rounds', params);
  }

  /* --------------------------------- Network --------------------------------- */

  /** GET /api/peers */
  listPeers(): Promise<PeerInfo[]> {
    return this.get('/peers');
  }

  /** GET /api/poies */
  getPoies(): Promise<PoIESMetrics> {
    return this.get('/poies');
  }
}

/* --------------------------------- Helpers ---------------------------------- */

/**
 * Construct an ExplorerApi from env-like values (Vite compatible).
 * Uses:
 *  - VITE_EXPLORER_API (base URL)
 *  - VITE_EXPLORER_API_KEY (optional)
 */
export function explorerApiFromEnv(env: {
  VITE_EXPLORER_API?: string;
  VITE_EXPLORER_API_KEY?: string;
}): ExplorerApi {
  const base = env.VITE_EXPLORER_API || (import.meta as any)?.env?.VITE_EXPLORER_API;
  if (!base) throw new Error('VITE_EXPLORER_API is required to use ExplorerApi');
  const apiKey =
    env.VITE_EXPLORER_API_KEY || (import.meta as any)?.env?.VITE_EXPLORER_API_KEY || undefined;

  return new ExplorerApi({
    baseUrl: base,
    apiKey,
    authScheme: 'header',
  });
}
