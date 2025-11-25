/**
 * Chains registry loader (build-time)
 * -----------------------------------
 * Imports chain metadata JSON files from `/chains/` at build time using Vite's
 * `import.meta.glob`. Each JSON file may export either a single chain record
 * or an array of chain records. The data is embedded into the bundle.
 *
 * Expected JSON shape per record:
 * {
 *   "chainId": 1,
 *   "caip2": "animica:1",
 *   "name": "Animica Mainnet",
 *   "status": "mainnet" | "testnet" | "devnet",
 *   "currency": "ANM",
 *   "rpcURL": "https://rpc.animica.org",
 *   "explorerURL": "https://explorer.animica.org",
 *   "color": "#0ea5e9",
 *   "features": ["pq-signatures", "useful-work", "python-vm"]
 * }
 */

import { ENV } from '../env';

export type ChainStatus = 'mainnet' | 'testnet' | 'devnet';

export interface ChainRecord {
  chainId: number;
  caip2: string; // e.g., "animica:1"
  name: string;
  status: ChainStatus;
  currency: string; // native currency ticker, e.g., "ANM"
  rpcURL: string;
  explorerURL?: string;
  color?: string;
  features?: string[];
}

export interface ChainRegistry {
  list: ChainRecord[];
  byId: Record<number, ChainRecord>;
  byCaip2: Record<string, ChainRecord>;
  default: ChainRecord;
}

/* ---------------------------------- Load ---------------------------------- */

type RawModuleMap = Record<string, string>;

/**
 * Load all JSON files from /chains/*.json eagerly as raw strings,
 * then JSON.parse them. If the folder is empty, we fall back to a synthesized
 * devnet entry using PUBLIC_RPC_URL and PUBLIC_CHAIN_ID from ENV.
 */
function loadChains(): ChainRecord[] {
  // Absolute-from-project-root. For an Astro app rooted at /website,
  // this points to /website/chains/*.json
  const files: RawModuleMap = import.meta.glob('/chains/*.json', {
    as: 'raw',
    eager: true,
  }) as RawModuleMap;

  const records: ChainRecord[] = [];

  const normalize = (x: unknown, src: string): ChainRecord[] => {
    if (Array.isArray(x)) return x.map((v, i) => validateChain(v, `${src}[${i}]`));
    return [validateChain(x as Record<string, unknown>, src)];
  };

  for (const [path, raw] of Object.entries(files)) {
    try {
      const parsed = JSON.parse(raw);
      records.push(...normalize(parsed, path));
    } catch (err) {
      console.error(`[chains] Failed to parse JSON at ${path}:`, err);
      throw new Error(`Invalid JSON in ${path}`);
    }
  }

  if (records.length === 0) {
    // Sensible fallback for dev environments with no /chains/ data yet.
    records.push({
      chainId: ENV.CHAIN_ID,
      caip2: `animica:${ENV.CHAIN_ID}`,
      name: ENV.CHAIN_ID === 1 ? 'Animica Mainnet' : `Animica Dev Chain ${ENV.CHAIN_ID}`,
      status: ENV.CHAIN_ID === 1 ? 'mainnet' : 'devnet',
      currency: 'ANM',
      rpcURL: ENV.RPC_URL,
      explorerURL: '', // optional
      color: '#0ea5e9',
      features: ['pq-signatures', 'useful-work', 'python-vm'],
    });
  }

  // Deduplicate by chainId (last write wins), then by caip2
  const byId: Record<number, ChainRecord> = {};
  const byCaip2: Record<string, ChainRecord> = {};
  for (const r of records) {
    byId[r.chainId] = r;
    byCaip2[r.caip2] = r;
  }

  // Stable ordering: mainnet → testnet → devnet, then by chainId asc
  const order = { mainnet: 0, testnet: 1, devnet: 2 } as const;
  records.sort((a, b) => {
    const s = order[a.status] - order[b.status];
    return s !== 0 ? s : a.chainId - b.chainId;
    });

  // Choose default: ENV.CHAIN_ID if present, otherwise first entry
  const def = byId[ENV.CHAIN_ID] ?? records[0];

  // Return final list (deduped)
  const uniq = Object.values(byId).sort((a, b) => {
    const s = order[a.status] - order[b.status];
    return s !== 0 ? s : a.chainId - b.chainId;
  });

  return uniq;
}

/* ------------------------------- Validation -------------------------------- */

function isPositiveInt(n: unknown): n is number {
  return typeof n === 'number' && Number.isInteger(n) && n > 0;
}

function validateURL(s: unknown, field: string, optional = false): string | undefined {
  if (optional && (s === undefined || s === '')) return undefined;
  if (typeof s !== 'string' || s.trim() === '') {
    throw new Error(`chains: ${field} must be a non-empty string URL`);
  }
  try {
    const u = new URL(s);
    if (!u.protocol || !u.host) throw new Error('not absolute');
    return u.toString().replace(/\/+$/, '');
  } catch {
    throw new Error(`chains: ${field} is not a valid absolute URL: ${String(s)}`);
  }
}

function validateChain(obj: Record<string, unknown>, src: string): ChainRecord {
  const chainId = obj.chainId;
  const caip2 = obj.caip2;
  const name = obj.name;
  const status = obj.status;
  const currency = obj.currency;
  const rpcURL = obj.rpcURL;
  const explorerURL = obj.explorerURL;
  const color = obj.color;
  const features = obj.features;

  if (!isPositiveInt(chainId)) throw new Error(`chains: ${src} chainId must be a positive integer`);
  if (typeof caip2 !== 'string' || !/^[a-z0-9_-]+:\d+$/.test(caip2))
    throw new Error(`chains: ${src} caip2 must look like "namespace:chainId"`);
  if (typeof name !== 'string' || name.trim() === '')
    throw new Error(`chains: ${src} name must be a non-empty string`);
  if (status !== 'mainnet' && status !== 'testnet' && status !== 'devnet')
    throw new Error(`chains: ${src} status must be one of mainnet|testnet|devnet`);
  if (typeof currency !== 'string' || currency.trim() === '')
    throw new Error(`chains: ${src} currency must be a non-empty string`);

  const rpc = validateURL(rpcURL, `${src}.rpcURL`)!;
  const explorer = validateURL(explorerURL, `${src}.explorerURL`, true);

  if (color !== undefined && (typeof color !== 'string' || !/^#([0-9a-fA-F]{3}|[0-9a-fA-F]{6})$/.test(color))) {
    throw new Error(`chains: ${src} color must be a hex like #0ea5e9`);
  }

  if (features !== undefined && !Array.isArray(features)) {
    throw new Error(`chains: ${src} features must be an array of strings if provided`);
  }

  return {
    chainId,
    caip2,
    name,
    status,
    currency,
    rpcURL: rpc,
    explorerURL: explorer,
    color,
    features: features as string[] | undefined,
  };
}

/* --------------------------------- Export ---------------------------------- */

const CHAINS_LIST = loadChains();

const BY_ID = Object.freeze(
  CHAINS_LIST.reduce<Record<number, ChainRecord>>((acc, c) => {
    acc[c.chainId] = c;
    return acc;
  }, {})
);

const BY_CAIP2 = Object.freeze(
  CHAINS_LIST.reduce<Record<string, ChainRecord>>((acc, c) => {
    acc[c.caip2] = c;
    return acc;
  }, {})
);

/** Immutable chains registry computed at build time. */
export const CHAINS: ChainRegistry = Object.freeze({
  list: CHAINS_LIST,
  byId: BY_ID,
  byCaip2: BY_CAIP2,
  default: BY_ID[ENV.CHAIN_ID] ?? CHAINS_LIST[0],
});

/** Helper getters */
export const getChains = (): readonly ChainRecord[] => CHAINS.list;
export const getChainById = (id: number): ChainRecord | undefined => CHAINS.byId[id];
export const getChainByCaip2 = (id: string): ChainRecord | undefined => CHAINS.byCaip2[id];

