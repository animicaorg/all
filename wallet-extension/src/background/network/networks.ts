/**
 * Known Animica networks for the wallet.
 *
 * - Provides a typed registry of mainnet / testnet / devnet
 * - Allows optional override via Vite env (VITE_RPC_URL, VITE_CHAIN_ID, VITE_NETWORK_NAME)
 * - Used by background/network/rpc.ts and UI network selectors
 */

export type NetworkId =
  | "animica-mainnet"
  | "animica-testnet"
  | "animica-devnet"
  | "custom-env";

/** Minimal shape the rest of the wallet relies on */
export interface Network {
  id: NetworkId;
  name: string;
  chainId: number;
  rpcHttp: string;
  rpcWs?: string;
  explorer?: string;
  bech32Prefix: string; // address prefix (e.g., anim1...)
  currencySymbol: string; // native token symbol
  currencyDecimals: number; // display decimals
  features: {
    da: boolean;          // Data Availability
    aicf: boolean;        // AI/Quantum compute
    randomness: boolean;  // beacon available
    zkVerify: boolean;    // on-chain zk.verify available
    blobs: boolean;       // blob/rollup txs
  };
}

/** Helpers */
const asNumber = (v: string | undefined): number | undefined => {
  if (!v) return undefined;
  const n = Number(v);
  return Number.isFinite(n) ? n : undefined;
};
const isHttpLike = (s: string | undefined) =>
  !!s && /^(http|https):\/\//i.test(s);

/**
 * Built-ins. Replace the example hostnames with your actual endpoints in ops/.
 * These are safe defaults for local dev + illustrative placeholders for others.
 */
const BUILTIN: Record<Exclude<NetworkId, "custom-env">, Network> = {
  "animica-mainnet": {
    id: "animica-mainnet",
    name: "Animica Mainnet",
    chainId: 1,
    rpcHttp: "https://rpc.mainnet.animica.example", // replace in ops config
    rpcWs: "wss://ws.mainnet.animica.example",
    explorer: "https://explorer.animica.example",
    bech32Prefix: "anim",
    currencySymbol: "ANM",
    currencyDecimals: 18,
    features: { da: true, aicf: true, randomness: true, zkVerify: true, blobs: true },
  },
  "animica-testnet": {
    id: "animica-testnet",
    name: "Animica Testnet",
    chainId: 1337,
    rpcHttp: "https://rpc.testnet.animica.example",
    rpcWs: "wss://ws.testnet.animica.example",
    explorer: "https://explorer.testnet.animica.example",
    bech32Prefix: "anim",
    currencySymbol: "ANMT",
    currencyDecimals: 18,
    features: { da: true, aicf: true, randomness: true, zkVerify: true, blobs: true },
  },
  "animica-devnet": {
    id: "animica-devnet",
    name: "Animica Devnet (local)",
    chainId: 1337,
    // JSON-RPC POST handler lives at /rpc on the devnet node
    rpcHttp: "http://localhost:8545/rpc",
    rpcWs: "ws://localhost:8546",
    explorer: "http://localhost:8080",
    bech32Prefix: "anim",
    currencySymbol: "ANMD",
    currencyDecimals: 18,
    features: { da: true, aicf: true, randomness: true, zkVerify: true, blobs: true },
  },
};

/**
 * Env override (optional).
 * If VITE_RPC_URL and VITE_CHAIN_ID are defined, we expose a "custom-env" network.
 */
const envRpc = (import.meta as any)?.env?.VITE_RPC_URL as string | undefined;
const envChainId = asNumber((import.meta as any)?.env?.VITE_CHAIN_ID as string | undefined);
const envName = ((import.meta as any)?.env?.VITE_NETWORK_NAME as string | undefined)?.trim();

const CUSTOM_ENV: Network | undefined =
  isHttpLike(envRpc) && typeof envChainId === "number"
    ? {
        id: "custom-env",
        name: envName || "Custom (env)",
        chainId: envChainId!,
        rpcHttp: envRpc!,
        rpcWs: undefined,
        explorer: undefined,
        bech32Prefix: "anim",
        currencySymbol: "ANM",
        currencyDecimals: 18,
        features: { da: true, aicf: true, randomness: true, zkVerify: true, blobs: true },
      }
    : undefined;

/** Exported registry */
export const KNOWN_NETWORKS: Network[] = [
  BUILTIN["animica-mainnet"],
  BUILTIN["animica-testnet"],
  BUILTIN["animica-devnet"],
  ...(CUSTOM_ENV ? [CUSTOM_ENV] : []),
];

export const NETWORK_MAP: Record<NetworkId, Network> = KNOWN_NETWORKS.reduce(
  (acc, n) => {
    acc[n.id] = n;
    return acc;
  },
  {} as Record<NetworkId, Network>
);

/**
 * Pick default network:
 * - If env override exists, prefer it
 * - Else prefer testnet for safety in non-production builds
 */
export function getDefaultNetworkId(): NetworkId {
  if (CUSTOM_ENV) return "custom-env";
  // You can switch this to "animica-mainnet" for production builds.
  return "animica-testnet";
}

export function getNetwork(id: NetworkId): Network {
  const n = NETWORK_MAP[id];
  if (!n) {
    throw new Error(`Unknown network id: ${id}`);
  }
  return n;
}

/** Convenience: find network by chainId (used on connect from dapps) */
export function findByChainId(chainId: number): Network | undefined {
  return KNOWN_NETWORKS.find((n) => n.chainId === chainId);
}
