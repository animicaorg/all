import { ENV } from "../env";

/**
 * Chain metadata loader
 * ---------------------
 * Reads JSON files from /chains/*.json (at build time) and normalizes a common
 * shape used across the site. The source files follow the schema described in
 * website/chains/README.md and may include extra fields that are preserved in
 * the output records.
 */

export interface ChainRecord {
  id: string;
  chainId: number;
  name: string;
  rpc: string;
  rpcUrls: string[];
  explorerUrl?: string;
  caip2?: string;
  testnet?: boolean;
  docs?: string;
  faucets?: string[];
  symbol?: string;
  /**
   * Preserve any additional metadata present in the source file so consumers
   * can opt into new fields without changing this loader.
   */
  [key: string]: unknown;
}

type RawModuleMap = Record<string, string>;

const CHAINS_GLOB = import.meta.glob("/chains/*.json", {
  as: "raw",
  eager: true,
}) as RawModuleMap;

function inferIdFromPath(path: string): string {
  const stem = path.split("/").pop() ?? path;
  return stem.replace(/\.json$/i, "");
}

function toStringArray(value: unknown): string[] {
  if (!value) return [];
  if (Array.isArray(value)) return value.map((v) => String(v)).filter(Boolean);
  return [String(value)].filter(Boolean);
}

function pickExplorer(raw: any): string | undefined {
  return (
    raw?.explorerUrl ||
    raw?.explorerURL ||
    raw?.explorer ||
    raw?.blockExplorerUrls?.[0]
  );
}

function pickRpcUrls(raw: any): string[] {
  const candidates: unknown[] = [
    raw?.rpcUrls?.public?.http,
    raw?.rpcUrls?.http,
    raw?.rpcUrls,
    raw?.rpc,
    raw?.rpcURL,
    raw?.rpcUrl,
  ];

  const flattened = candidates.flatMap(toStringArray).filter(Boolean);
  const seen = new Set<string>();
  const uniq: string[] = [];
  for (const url of flattened) {
    if (seen.has(url)) continue;
    seen.add(url);
    uniq.push(url);
  }
  return uniq;
}

function normalizeChain(raw: any, path: string): ChainRecord | null {
  if (!raw || typeof raw !== "object") return null;

  const chainId = Number((raw as any).chainId);
  if (!Number.isFinite(chainId) || chainId <= 0) return null;

  const id = typeof raw.id === "string" && raw.id.trim() ? raw.id : inferIdFromPath(path);
  const name = typeof raw.name === "string" && raw.name.trim() ? raw.name : `Chain ${chainId}`;

  const rpcUrls = pickRpcUrls(raw);
  const rpc = rpcUrls[0] ?? "";

  const explorerUrl = pickExplorer(raw);
  const symbol = raw.symbol ?? raw.currency ?? raw.ticker;
  const caip2 = typeof raw.caip2 === "string" ? raw.caip2 : `animica:${chainId}`;
  const testnet = typeof raw.testnet === "boolean" ? raw.testnet : undefined;

  const faucets = Array.isArray(raw.faucets) ? raw.faucets.map(String) : undefined;

  return {
    ...raw,
    id,
    chainId,
    name,
    rpc,
    rpcUrls,
    rpcUrl: rpc,
    explorerUrl,
    caip2,
    symbol,
    testnet,
    faucets,
  } satisfies ChainRecord;
}

function loadChains(): ChainRecord[] {
  const records: ChainRecord[] = [];

  for (const [path, raw] of Object.entries(CHAINS_GLOB)) {
    try {
      const parsed = JSON.parse(raw);
      const normalized = normalizeChain(parsed, path);
      if (normalized) records.push(normalized);
    } catch (err) {
      console.error(`[chains] Failed to parse JSON at ${path}:`, err);
    }
  }

  if (records.length === 0) {
    const fallbackRpc = ENV.RPC_URL ? [ENV.RPC_URL] : [];
    records.push({
      id: `animica:${ENV.CHAIN_ID}`,
      chainId: ENV.CHAIN_ID,
      name: ENV.CHAIN_ID === 1 ? "Animica Mainnet" : `Animica Dev Chain ${ENV.CHAIN_ID}`,
      rpc: fallbackRpc[0] ?? "",
      rpcUrls: fallbackRpc,
      explorerUrl: undefined,
      caip2: `animica:${ENV.CHAIN_ID}`,
      testnet: ENV.CHAIN_ID !== 1,
      docs: undefined,
      faucets: [],
    });
  }

  // stable sort by numeric chainId then id
  records.sort((a, b) => {
    if (a.chainId !== b.chainId) return a.chainId - b.chainId;
    return String(a.id).localeCompare(String(b.id));
  });

  return records;
}

export const chains = loadChains();
export const chainsById = chains.reduce<Record<number, ChainRecord>>((acc, c) => {
  acc[c.chainId] = c;
  return acc;
}, {});
export const defaultChain = chainsById[ENV.CHAIN_ID] ?? chains[0];

export default chains;

