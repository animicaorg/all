/**
 * Account slice — connected wallet/account state (window.animica provider).
 *
 * Responsibilities:
 * - Detect the injected provider (wallet-extension) and expose status.
 * - Connect/disconnect, track selected address and chainId.
 * - React to provider events (accountsChanged, chainChanged).
 * - Provide a tiny balance refresher via node JSON-RPC (state.getBalance).
 *
 * This slice DOES NOT hold private keys. All signing lives in the wallet extension.
 * We follow an AIP-1193-like provider: window.animica.request({ method, params? }).
 */

import useStore, { registerSlice, type SliceCreator, type StoreState, type SetState, type GetState } from './store';
import type { NetworkSlice } from './network';

type ProviderStatus = 'unknown' | 'available' | 'unavailable';

export interface AnimicaProvider {
  request<T = unknown>(args: { method: string; params?: unknown[] | Record<string, unknown> }): Promise<T>;
  on?(event: 'accountsChanged' | 'chainChanged' | string, handler: (...args: any[]) => void): void;
  removeListener?(event: 'accountsChanged' | 'chainChanged' | string, handler: (...args: any[]) => void): void;
}

declare global {
  interface Window {
    animica?: AnimicaProvider;
  }
}

export type AccountSlice = {
  providerStatus: ProviderStatus;
  isConnected: boolean;
  connecting: boolean;

  address?: string;
  chainId?: number;
  balance?: string; // decimal string; UI formats it

  networkMismatch: boolean; // provider chainId !== app selected chainId
  lastError?: string;

  detectProvider: () => void;
  tryReconnect: () => Promise<void>;
  connect: () => Promise<void>;
  disconnect: () => void;
  refreshBalance: () => Promise<void>;
  setAddress: (addr?: string) => void;
  setChainId: (id?: number) => void;
};

function provider(): AnimicaProvider | undefined {
  return typeof window !== 'undefined' ? window.animica : undefined;
}

function rpcEndpoint(base: string): string {
  try {
    const u = new URL(base);
    // Node serves JSON-RPC at /rpc
    if (u.pathname.endsWith('/rpc')) return u.toString();
    u.pathname = (u.pathname.replace(/\/+$/, '') || '') + '/rpc';
    return u.toString();
  } catch {
    // Best-effort fallback
    const trimmed = base.replace(/\/+$/, '');
    return trimmed.endsWith('/rpc') ? trimmed : trimmed + '/rpc';
  }
}

async function jsonRpc<T = unknown>(url: string, method: string, params: unknown): Promise<T> {
  const body = { jsonrpc: '2.0', id: Math.floor(Math.random() * 1e9), method, params };
  const res = await fetch(url, {
    method: 'POST',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify(body),
  });
  if (!res.ok) throw new Error(`RPC ${method} HTTP ${res.status}`);
  const msg = await res.json();
  if (msg.error) throw new Error(msg.error?.message ?? 'RPC error');
  return msg.result as T;
}

function parseChainIdLike(v: unknown): number | undefined {
  if (typeof v === 'number' && Number.isFinite(v)) return v;
  if (typeof v === 'string') {
    // Accept hex "0x1" or decimal "1"
    if (v.startsWith('0x') || v.startsWith('0X')) {
      const n = Number.parseInt(v, 16);
      return Number.isFinite(n) ? n : undefined;
    }
    const n = Number.parseInt(v, 10);
    return Number.isFinite(n) ? n : undefined;
  }
  return undefined;
}

const accountSlice: SliceCreator<AccountSlice> = (set: SetState<StoreState>, get: GetState<StoreState>) => {
  let boundHandlers = false;
  const onAccountsChanged = (accounts: string[]) => {
    const addr = Array.isArray(accounts) && accounts.length ? String(accounts[0]) : undefined;
    set({ address: addr, isConnected: !!addr } as Partial<StoreState>);
  };
  const onChainChanged = (cid: unknown) => {
    const newId = parseChainIdLike(cid);
    const appChain = (get() as unknown as NetworkSlice).currentChainId();
    set({
      chainId: newId,
      networkMismatch: typeof newId === 'number' ? newId !== appChain : false,
    } as Partial<StoreState>);
  };

  function bindProviderEvents(p?: AnimicaProvider) {
    if (boundHandlers || !p?.on) return;
    p.on('accountsChanged', onAccountsChanged);
    p.on('chainChanged', onChainChanged);
    boundHandlers = true;
  }

  return {
    providerStatus: 'unknown',
    isConnected: false,
    connecting: false,
    address: undefined,
    chainId: undefined,
    balance: undefined,
    networkMismatch: false,
    lastError: undefined,

    detectProvider: () => {
      const p = provider();
      set({ providerStatus: p ? 'available' : 'unavailable' } as Partial<StoreState>);
      bindProviderEvents(p);
    },

    tryReconnect: async () => {
      const p = provider();
      set({ providerStatus: p ? 'available' : 'unavailable' } as Partial<StoreState>);
      if (!p) return;
      bindProviderEvents(p);
      try {
        // Query existing accounts without user prompt
        const methods = ['animica_accounts', 'eth_accounts'] as const;
        let accounts: string[] = [];
        for (const m of methods) {
          try {
            // eslint-disable-next-line no-await-in-loop
            accounts = (await p.request({ method: m })) as string[];
            if (accounts && accounts.length) break;
          } catch { /* try next */ }
        }
        const addr = accounts?.[0];
        const chainCandidates = ['animica_chainId', 'eth_chainId'] as const;
        let chainId: number | undefined;
        for (const m of chainCandidates) {
          try {
            // eslint-disable-next-line no-await-in-loop
            const v = await p.request({ method: m });
            chainId = parseChainIdLike(v);
            if (chainId) break;
          } catch { /* try next */ }
        }
        const appChain = (get() as unknown as NetworkSlice).currentChainId();
        set({
          isConnected: !!addr,
          address: addr,
          chainId,
          networkMismatch: typeof chainId === 'number' ? chainId !== appChain : false,
          lastError: undefined,
        } as Partial<StoreState>);
      } catch (err: any) {
        set({ lastError: String(err?.message ?? err) } as Partial<StoreState>);
      }
    },

    connect: async () => {
      const p = provider();
      set({ providerStatus: p ? 'available' : 'unavailable', connecting: true, lastError: undefined } as Partial<StoreState>);
      if (!p) {
        set({ connecting: false, lastError: 'Wallet not found. Please install/enable the Animica wallet extension.' } as Partial<StoreState>);
        return;
      }
      bindProviderEvents(p);
      try {
        // Prompt user for permission
        let accounts: string[] = [];
        const candidates = ['wallet_requestAccounts', 'animica_requestAccounts', 'eth_requestAccounts'] as const;
        for (const m of candidates) {
          try {
            // eslint-disable-next-line no-await-in-loop
            accounts = (await p.request({ method: m })) as string[];
            if (accounts && accounts.length) break;
          } catch { /* continue */ }
        }
        const addr = accounts?.[0];
        if (!addr) throw new Error('No account returned by wallet');
        // Read provider chainId
        const chainCandidates = ['animica_chainId', 'eth_chainId'] as const;
        let chainId: number | undefined;
        for (const m of chainCandidates) {
          try {
            // eslint-disable-next-line no-await-in-loop
            const v = await p.request({ method: m });
            chainId = parseChainIdLike(v);
            if (chainId) break;
          } catch { /* continue */ }
        }
        const appChain = (get() as unknown as NetworkSlice).currentChainId();
        set({
          isConnected: true,
          address: addr,
          chainId,
          networkMismatch: typeof chainId === 'number' ? chainId !== appChain : false,
          connecting: false,
          lastError: undefined,
        } as Partial<StoreState>);
      } catch (err: any) {
        set({
          connecting: false,
          lastError: String(err?.message ?? err),
          isConnected: false,
        } as Partial<StoreState>);
      }
    },

    disconnect: () => {
      // We cannot force the extension to disconnect; we just clear local app state.
      const p = provider();
      if (p?.removeListener) {
        p.removeListener('accountsChanged', onAccountsChanged);
        p.removeListener('chainChanged', onChainChanged);
      }
      set({
        isConnected: false,
        address: undefined,
        chainId: undefined,
        balance: undefined,
        networkMismatch: false,
      } as Partial<StoreState>);
    },

    refreshBalance: async () => {
      const addr = (get() as unknown as AccountSlice).address;
      if (!addr) return;
      const rpcUrl = (get() as unknown as NetworkSlice).rpcHttpUrl();
      try {
        const url = rpcEndpoint(rpcUrl);
        const result = await jsonRpc<string>(url, 'state.getBalance', [addr]);
        // Expect a decimal string (node returns canonical string); accept hex and convert if needed.
        let bal = result;
        if (typeof bal === 'string' && /^0x[0-9a-fA-F]+$/.test(bal)) {
          bal = BigInt(bal).toString(10);
        }
        set({ balance: String(bal), lastError: undefined } as Partial<StoreState>);
      } catch (err: any) {
        set({ lastError: `balance: ${String(err?.message ?? err)}` } as Partial<StoreState>);
      }
    },

    setAddress: (addr?: string) => set({ address: addr, isConnected: !!addr } as Partial<StoreState>),
    setChainId: (id?: number) => {
      const appChain = (get() as unknown as NetworkSlice).currentChainId();
      set({
        chainId: id,
        networkMismatch: typeof id === 'number' ? id !== appChain : false,
      } as Partial<StoreState>);
    },
  };
};

registerSlice<AccountSlice>(accountSlice);

export function useAccount<T = AccountSlice & { connected: boolean }>(selector?: (s: AccountSlice & { connected: boolean }) => T): T {
  return useStore((s) => {
    const slice = s as unknown as AccountSlice;
    const withAlias = { ...slice, connected: slice.isConnected };
    return selector ? selector(withAlias) : (withAlias as T);
  });
}

export function useAccountState() {
  return useAccount((s) => ({
    providerStatus: s.providerStatus,
    isConnected: s.isConnected,
    connecting: s.connecting,
    address: s.address,
    chainId: s.chainId,
    balance: s.balance,
    networkMismatch: s.networkMismatch,
    lastError: s.lastError,
  }));
}

export default undefined;
