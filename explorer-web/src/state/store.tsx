/**
 * Animica Explorer — Root Store
 * -----------------------------------------------------------------------------
 * Lightweight global state built on Zustand (vanilla store + React context).
 * - Typed slices for network, head, UI, and toasts
 * - Devtools in development
 * - Persistent keys (network, UI) via localStorage
 * - Selector-driven React hook with optional equality
 *
 * Usage:
 *   <ExplorerStoreProvider>
 *     <App/>
 *   </ExplorerStoreProvider>
 *
 *   const chainId = useExplorerStore(s => s.network.chainId)
 *   const addToast = useExplorerStore(s => s.addToast)
 */

import React, { createContext, useContext, useRef } from 'react';
import type { ReactNode } from 'react';
import { useStore as useZustandStore } from 'zustand';
import { createStore, type StoreApi } from 'zustand/vanilla';
import { devtools, persist, subscribeWithSelector, createJSONStorage } from 'zustand/middleware';
import shallow from 'zustand/shallow';
import { inferChainId, inferRpcUrl } from '../services/env';

// ----------------------------- Types ----------------------------------------

export type Theme = 'light' | 'dark' | 'system';

export type ToastKind = 'info' | 'success' | 'warning' | 'error';

export interface Toast {
  id: string;
  kind: ToastKind;
  text: string;
  ts: number;     // epoch millis
  ttl?: number;   // optional time-to-live (ms)
}

export interface NetworkState {
  rpcUrl: string;
  chainId: string;
  connected: boolean;
}

export interface HeadState {
  height: number;
  hash: string;
  timeISO: string; // RFC 3339 timestamp
}

export interface UIState {
  theme: Theme;
}

export interface ExplorerState {
  // Slices
  network: NetworkState;
  head: HeadState;
  ui: UIState;
  toasts: Toast[];

  // Actions
  setNetwork: (patch: Partial<NetworkState>) => void;
  setHead: (patch: Partial<HeadState>) => void;

  setTheme: (theme: Theme) => void;

  addToast: (input: { kind?: ToastKind; text: string; ttl?: number } | Toast) => string;
  removeToast: (id: string) => void;
  clearToasts: () => void;
}

// --------------------------- Defaults ---------------------------------------

const envRpc = inferRpcUrl((import.meta as any)?.env);
const envChainId = inferChainId((import.meta as any)?.env);

const defaults = (): ExplorerState => ({
  network: {
    rpcUrl: envRpc,
    chainId: envChainId,
    connected: false,
  },
  head: {
    height: 0,
    hash: '',
    timeISO: new Date(0).toISOString(),
  },
  ui: {
    theme: 'system',
  },
  toasts: [],

  setNetwork: () => {},
  setHead: () => {},
  setTheme: () => {},

  addToast: () => '',
  removeToast: () => {},
  clearToasts: () => {},
});

// ----------------------- Store Construction ---------------------------------

/**
 * Create the underlying Zustand store with middlewares.
 * Optionally hydrate with a partial state (e.g., during tests).
 */
export function createExplorerStore(preloaded?: Partial<ExplorerState>) {
  // Choose storage safely (Node/test env may lack localStorage).
  const storage = createJSONStorage<ExplorerState>(() => {
    try {
      return window.localStorage;
    } catch {
      // Fallback memory storage for SSR/tests
      let mem: Record<string, string> = {};
      return {
        getItem: (k: string) => mem[k] ?? null,
        setItem: (k: string, v: string) => { mem[k] = v; },
        removeItem: (k: string) => { delete mem[k]; },
      };
    }
  });

  type S = ExplorerState;

  const withMiddlewares = subscribeWithSelector<S>(
    devtools(
      persist<S>(
        (set, get) => {
          const d = { ...defaults(), ...preloaded };

          return {
            ...d,

            setNetwork: (patch) =>
              set(
                (s) => ({ network: { ...s.network, ...patch } }),
                false,
                'network/setNetwork'
              ),

            setHead: (patch) =>
              set(
                (s) => ({ head: { ...s.head, ...patch } }),
                false,
                'head/setHead'
              ),

            setTheme: (theme) =>
              set(
                (s) => ({ ui: { ...s.ui, theme } }),
                false,
                'ui/setTheme'
              ),

            addToast: (input) => {
              const t: Toast =
                'id' in input
                  ? input
                  : {
                      id: cryptoRandomId(),
                      kind: input.kind ?? 'info',
                      text: input.text,
                      ts: Date.now(),
                      ttl: input.ttl,
                    };
              set((s) => ({ toasts: [t, ...s.toasts].slice(0, 200) }), false, 'toast/add');
              // Optional auto-expire
              if (t.ttl && typeof window !== 'undefined') {
                window.setTimeout(() => {
                  const exists = get().toasts.some((x) => x.id === t.id);
                  if (exists) get().removeToast(t.id);
                }, t.ttl);
              }
              return t.id;
            },

            removeToast: (id) =>
              set((s) => ({ toasts: s.toasts.filter((t) => t.id !== id) }), false, 'toast/remove'),

            clearToasts: () => set({ toasts: [] }, false, 'toast/clear'),
          };
        },
        {
          name: 'animica-explorer-store',
          version: 1,
          storage,
          // Persist only safe, user-facing preferences:
          partialize: (state) =>
            ({
              network: {
                rpcUrl: state.network.rpcUrl,
                chainId: state.network.chainId,
                connected: false, // never persist live connection flag
              },
              ui: state.ui,
            } as unknown as S),
          onRehydrateStorage: () => (state, error) => {
            if (error) {
              // eslint-disable-next-line no-console
              console.warn('[store] rehydrate error', error);
            }
          },
        }
      ),
      { name: 'Animica Explorer Store' }
    )
  );

  return createStore<S>()(withMiddlewares);
}

// ---------------------------- React Hook ------------------------------------

const ExplorerStoreContext = createContext<StoreApi<ExplorerState> | null>(null);

export function ExplorerStoreProvider({
  children,
  preloadedState,
}: {
  children: ReactNode;
  preloadedState?: Partial<ExplorerState>;
}) {
  const storeRef = useRef<StoreApi<ExplorerState>>();
  if (!storeRef.current) {
    storeRef.current = createExplorerStore(preloadedState);
  }
  return (
    <ExplorerStoreContext.Provider value={storeRef.current}>
      {children}
    </ExplorerStoreContext.Provider>
  );
}

/**
 * Typed selector hook with optional equality comparator.
 *
 *   const value = useExplorerStore(s => s.network.chainId)
 *   const shallowSlice = useExplorerStore(s => ({ a: s.a, b: s.b }), shallow)
 */
export function useExplorerStore<T>(
  selector: (state: ExplorerState) => T,
  equality?: (a: T, b: T) => boolean
): T {
  const store = useContext(ExplorerStoreContext);
  if (!store) {
    throw new Error('useExplorerStore must be used within <ExplorerStoreProvider>');
  }
  // @ts-expect-error: zustand react signature supports equality as third arg
  return useZustandStore(store, selector, equality);
}

// Commonly re-used selectors & helpers
export const selectors = {
  network: (s: ExplorerState) => s.network,
  head: (s: ExplorerState) => s.head,
  theme: (s: ExplorerState) => s.ui.theme,
  toasts: (s: ExplorerState) => s.toasts,
};

export { shallow };

// ------------------------------ Utils ---------------------------------------

function cryptoRandomId(): string {
  try {
    const b = new Uint8Array(12);
    (typeof crypto !== 'undefined' ? crypto : require('crypto').webcrypto).getRandomValues(b);
    // base32-ish without padding
    return Array.from(b)
      .map((x) => x.toString(16).padStart(2, '0'))
      .join('');
  } catch {
    return Math.random().toString(36).slice(2);
  }
}
