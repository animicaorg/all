/**
 * Network slice — RPC URL, chainId, services URL, and presets.
 *
 * Responsibilities:
 * - Hold the currently selected network (rpcUrl, chainId, label, wsUrl, servicesUrl).
 * - Provide sane defaults from environment (.env: VITE_RPC_URL, VITE_CHAIN_ID, VITE_SERVICES_URL).
 * - Offer a few presets (local/devnet/testnet) and allow custom additions.
 * - Persist the current selection to localStorage and rehydrate on boot.
 */

import useStore, { registerSlice, type SliceCreator, type StoreState, type SetState, type GetState } from './store';

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

function isLoopback(url: string): boolean {
  try {
    const u = new URL(url);
    return u.hostname === 'localhost' || u.hostname === '127.0.0.1';
  } catch {
    return false;
  }
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

function sanitizeConfig(cfg: NetworkConfig): NetworkConfig {
  // Force env RPC when a loopback address sneaks in
  if (isLoopback(cfg.rpcUrl) && ENV_RPC && !isLoopback(ENV_RPC)) {
    return {
      ...cfg,
      label: cfg.label ?? 'Env Config',
      rpcUrl: ENV_RPC,
      wsUrl: deriveWsUrl(ENV_RPC),
      servicesUrl: ENV_SERVICES,
    };
  }

  return { ...cfg, wsUrl: cfg.wsUrl ?? deriveWsUrl(cfg.rpcUrl) };
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
    local: sanitizeConfig(DEFAULT_LOCAL),
    devnet: sanitizeConfig(DEFAULT_DEVNET),
    testnet: sanitizeConfig(DEFAULT_TESTNET),
    mainnet: sanitizeConfig(DEFAULT_MAINNET),
    env: sanitizeConfig(envPreset),
  };
}

function initialSelection(presets: Record<NetworkId, NetworkConfig>): NetworkConfig {
  const saved = rehydrateSelection();
  if (saved) return sanitizeConfig(saved);
  // Prefer env preset if env differs from default
  if (ENV_RPC && (ENV_RPC !== DEFAULT_LOCAL.rpcUrl || ENV_CHAIN !== DEFAULT_LOCAL.chainId)) {
    return sanitizeConfig(presets['env']);
  }
  return sanitizeConfig(presets['local']);
}

/* --------------------------------- slice --------------------------------- */

const createNetworkSlice: SliceCreator<NetworkSlice> = (set: SetState<StoreState>, get: GetState<StoreState>) => {
  const presets = initialPresets();
  const selected = initialSelection(presets);

  function commit(next: NetworkConfig) {
    const sanitized = sanitizeConfig(next);
    persistSelection(sanitized);
    set({ network: sanitized } as Partial<StoreState>);
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
        ps[cfg.id] = sanitizeConfig(cfg);
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

function defaultNetworkSelector(slice: NetworkSlice) {
  const { network, presets, setNetwork, setNetworkById, addOrUpdatePreset, removePreset, resetToDefault } = slice;
  return {
    rpcUrl: network.rpcUrl,
    chainId: network.chainId,
    servicesUrl: network.servicesUrl,
    presets: Object.values(presets),
    setNetwork,
    setNetworkById,
    addOrUpdatePreset,
    removePreset,
    resetToDefault,
  } as const;
}

export function useNetwork<T = ReturnType<typeof defaultNetworkSelector>>(selector?: (s: NetworkSlice) => T): T {
  return useStore((s) => (selector ? selector(s as unknown as NetworkSlice) : defaultNetworkSelector(s as unknown as NetworkSlice)));
}

export function useNetworkState() {
  return useNetwork((s) => ({ rpcUrl: s.network.rpcUrl, chainId: s.network.chainId, servicesUrl: s.network.servicesUrl }));
}

export type { NetworkConfig };
export default undefined;
