/**
 * Network slice — RPC URL, chainId, services URL, and presets.
 *
 * Responsibilities:
 * - Hold the currently selected network (rpcUrl, chainId, label, wsUrl, servicesUrl).
 * - Provide sane defaults from environment (.env: VITE_RPC_URL, VITE_CHAIN_ID, VITE_SERVICES_URL).
 * - Offer a few presets (local/devnet/testnet) and allow custom additions.
 * - Persist the current selection to localStorage and rehydrate on boot.
 */

import { registerSlice, type SliceCreator, type StoreState, type SetState, type GetState } from './store';

const STORAGE_KEY = 'studio-web.network.v1';

export type NetworkId = 'local' | 'devnet' | 'testnet' | 'mainnet' | string;

export interface NetworkConfig {
  /** Unique id for the preset / selection */
  id: NetworkId;
  /** Human label */
  label: string;
  /** CAIP-2 chain id number (animica mainnet=1, testnet=2, devnet=1337) */
  chainId: number;
  /** HTTP(S) JSON-RPC endpoint */
  rpcUrl: string;
  /** WS(S) endpoint (derived from rpcUrl if omitted) */
  wsUrl?: string;
  /** Studio services base URL (optional) */
  servicesUrl?: string;
  /** Optional explorer URL used by deep-links in the UI */
  explorerUrl?: string;
}

export type NetworkSlice = {
  network: NetworkConfig;
  presets: Record<NetworkId, NetworkConfig>;

  setNetwork: (cfg: Partial<NetworkConfig> & Pick<NetworkConfig, 'rpcUrl' | 'chainId'>) => void;
  setNetworkById: (id: NetworkId) => void;
  addOrUpdatePreset: (cfg: NetworkConfig) => void;
  removePreset: (id: NetworkId) => void;
  resetToDefault: () => void;

  /** Derived helpers */
  rpcHttpUrl: () => string;
  rpcWsUrl: () => string;
  currentChainId: () => number;
  currentLabel: () => string;
};

/* ----------------------------- env + helpers ----------------------------- */

function getEnv<T = string>(key: string, fallback?: T): T {
  // Vite injects import.meta.env.* at build-time
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const env = (import.meta as any)?.env ?? {};
  return (env[key] ?? fallback) as T;
}

function safeParseInt(v: unknown, fallback: number): number {
  const n = typeof v === 'string' ? parseInt(v, 10) : typeof v === 'number' ? v : NaN;
  return Number.isFinite(n) ? n : fallback;
}

function deriveWsUrl(httpUrl: string): string {
  try {
    const u = new URL(httpUrl);
    if (u.protocol === 'http:') u.protocol = 'ws:';
    if (u.protocol === 'https:') u.protocol = 'wss:';
    return u.toString();
  } catch {
    // As a last resort, naive replace
    return httpUrl.replace(/^http/i, 'ws');
  }
}

function persistSelection(n: NetworkConfig) {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(n));
  } catch {
    /* ignore */
  }
}

function rehydrateSelection(): NetworkConfig | null {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (!raw) return null;
    const parsed = JSON.parse(raw);
    // Minimal guard
    if (typeof parsed?.rpcUrl === 'string' && Number.isFinite(parsed?.chainId)) {
      return parsed as NetworkConfig;
    }
    return null;
  } catch {
    return null;
  }
}

/* -------------------------------- defaults ------------------------------- */

const ENV_RPC = getEnv<string>('VITE_RPC_URL', 'http://127.0.0.1:8545');
const ENV_CHAIN = safeParseInt(getEnv<string>('VITE_CHAIN_ID', '1337'), 1337);
const ENV_SERVICES = getEnv<string | undefined>('VITE_SERVICES_URL', undefined);

const DEFAULT_LOCAL: NetworkConfig = {
  id: 'local',
  label: 'Local Dev',
  chainId: 1337,
  rpcUrl: 'http://127.0.0.1:8545',
  wsUrl: 'ws://127.0.0.1:8546',
  servicesUrl: 'http://127.0.0.1:8787',
};

const DEFAULT_DEVNET: NetworkConfig = {
  id: 'devnet',
  label: 'Animica Devnet',
  chainId: 1337,
  rpcUrl: 'http://localhost:8545',
  wsUrl: 'ws://localhost:8546',
  servicesUrl: 'http://localhost:8787',
};

const DEFAULT_TESTNET: NetworkConfig = {
  id: 'testnet',
  label: 'Animica Testnet',
  chainId: 2,
  rpcUrl: 'https://rpc.testnet.animica.org',
  wsUrl: 'wss://ws.testnet.animica.org',
  servicesUrl: 'https://services.testnet.animica.org',
};

const DEFAULT_MAINNET: NetworkConfig = {
  id: 'mainnet',
  label: 'Animica Mainnet',
  chainId: 1,
  rpcUrl: 'https://rpc.animica.org',
  wsUrl: 'wss://ws.animica.org',
  servicesUrl: 'https://services.animica.org',
};

function initialPresets(): Record<NetworkId, NetworkConfig> {
  const envPreset: NetworkConfig = {
    id: 'env',
    label: 'Env Config',
    chainId: ENV_CHAIN,
    rpcUrl: ENV_RPC,
    wsUrl: deriveWsUrl(ENV_RPC),
    servicesUrl: ENV_SERVICES,
  };
  return {
    local: DEFAULT_LOCAL,
    devnet: DEFAULT_DEVNET,
    testnet: DEFAULT_TESTNET,
    mainnet: DEFAULT_MAINNET,
    env: envPreset,
  };
}

function initialSelection(presets: Record<NetworkId, NetworkConfig>): NetworkConfig {
  const saved = rehydrateSelection();
  if (saved) return { ...saved, wsUrl: saved.wsUrl ?? deriveWsUrl(saved.rpcUrl) };
  // Prefer env preset if env differs from default
  if (ENV_RPC && (ENV_RPC !== DEFAULT_LOCAL.rpcUrl || ENV_CHAIN !== DEFAULT_LOCAL.chainId)) {
    return presets['env'];
  }
  return presets['local'];
}

/* --------------------------------- slice --------------------------------- */

const createNetworkSlice: SliceCreator<NetworkSlice> = (set: SetState<StoreState>, get: GetState<StoreState>) => {
  const presets = initialPresets();
  const selected = initialSelection(presets);

  function commit(next: NetworkConfig) {
    persistSelection(next);
    set({ network: next } as Partial<StoreState>);
  }

  return {
    network: selected,
    presets,

    setNetwork: (cfg) => {
      const curr = (get() as unknown as NetworkSlice).network;
      const merged: NetworkConfig = {
        id: cfg.id ?? curr.id,
        label: cfg.label ?? curr.label ?? 'Custom',
        chainId: cfg.chainId,
        rpcUrl: cfg.rpcUrl,
        wsUrl: cfg.wsUrl ?? deriveWsUrl(cfg.rpcUrl),
        servicesUrl: cfg.servicesUrl ?? curr.servicesUrl,
        explorerUrl: cfg.explorerUrl ?? curr.explorerUrl,
      };
      commit(merged);
    },

    setNetworkById: (id: NetworkId) => {
      const p = (get() as unknown as NetworkSlice).presets[id];
      if (!p) throw new Error(`Unknown network preset: ${id}`);
      commit({ ...p, wsUrl: p.wsUrl ?? deriveWsUrl(p.rpcUrl) });
    },

    addOrUpdatePreset: (cfg: NetworkConfig) => {
      set((s) => {
        const ps = { ...(s as unknown as NetworkSlice).presets };
        ps[cfg.id] = { ...cfg, wsUrl: cfg.wsUrl ?? deriveWsUrl(cfg.rpcUrl) };
        return { presets: ps } as Partial<StoreState>;
      });
    },

    removePreset: (id: NetworkId) => {
      set((s) => {
        const ps = { ...(s as unknown as NetworkSlice).presets };
        delete ps[id];
        return { presets: ps } as Partial<StoreState>;
      });
    },

    resetToDefault: () => {
      const ps = initialPresets();
      set({ presets: ps } as Partial<StoreState>);
      commit(initialSelection(ps));
    },

    rpcHttpUrl: () => (get() as unknown as NetworkSlice).network.rpcUrl,
    rpcWsUrl: () => {
      const n = (get() as unknown as NetworkSlice).network;
      return n.wsUrl ?? deriveWsUrl(n.rpcUrl);
    },
    currentChainId: () => (get() as unknown as NetworkSlice).network.chainId,
    currentLabel: () => (get() as unknown as NetworkSlice).network.label,
  };
};

registerSlice<NetworkSlice>(createNetworkSlice);

export type { NetworkConfig };
export default undefined;
