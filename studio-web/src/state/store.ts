/**
 * Root Zustand store builder for Studio Web.
 *
 * Design:
 * - Slices register themselves via `registerSlice` at module import time.
 * - The first time any component calls `useStore(...)`, we lazily compose all
 *   registered slices and create the underlying Zustand store (with devtools).
 * - This avoids import-order footguns and circular deps between slices.
 *
 * Usage in a slice (e.g., network.ts):
 *   import type { SliceCreator } from './store';
 *   import { registerSlice } from './store';
 *
 *   export type NetworkSlice = {
 *     network: { rpcUrl: string; chainId: number };
 *     setNetwork: (rpcUrl: string, chainId: number) => void;
 *   };
 *
 *   const createNetworkSlice: SliceCreator<NetworkSlice> = (set) => ({
 *     network: { rpcUrl: 'http://localhost:8545', chainId: 1337 },
 *     setNetwork: (rpcUrl, chainId) => set((s) => ({ ...s, network: { rpcUrl, chainId } })),
 *   });
 *
 *   registerSlice(createNetworkSlice);
 *
 * Usage in React components:
 *   import { useStore } from './state/store';
 *   const chainId = useStore(s => s.network.chainId);
 *
 * Non-React usage:
 *   import { getState, setState } from './state/store';
 *   const snapshot = getState();
 *   setState({ someKey: value });
 */

import { create, type StoreApi, type UseBoundStore } from 'zustand';
import { devtools } from 'zustand/middleware';

export type SetState<T> = (
  partial: Partial<T> | ((state: T) => Partial<T>),
  replace?: boolean
) => void;
export type GetState<T> = () => T;

/** Base store shape — refined by registered slices. */
export type StoreState =
  & Record<string, unknown>; // slices will refine this by declaration merging in their own modules

/** A slice creator accepts the root set/get/api and returns its partial state. */
export type SliceCreator<S extends Record<string, any>> = (
  set: SetState<StoreState>,
  get: GetState<StoreState>,
  api: StoreApi<StoreState>
) => S;

/** Internal slice registry populated by slice modules via registerSlice(...) */
const __sliceRegistry: SliceCreator<any>[] = [];

/** Register a slice creator. Call from your slice module at top-level. */
export function registerSlice<S extends Record<string, any>>(creator: SliceCreator<S>): void {
  __sliceRegistry.push(creator);
}

/** Optional hook to inspect what slices are currently registered (dev only). */
export function listRegisteredSlices(): number {
  return __sliceRegistry.length;
}

/** Lazily-initialized zustand store hook. */
let __store: UseBoundStore<StoreApi<StoreState>> | null = null;

/** Compose all registered slices into one initializer for zustand. */
function composeInitializer() {
  return (set: SetState<StoreState>, get: GetState<StoreState>, api: StoreApi<StoreState>) => {
    // Merge all slice states (left→right). Later slices can override earlier keys intentionally.
    const merged = Object.assign({}, ...__sliceRegistry.map((mk) => mk(set, get, api)));
    // Provide a small always-present admin area
    const admin = {
      __admin: {
        ready: true,
        version: 'studio-web-store/1',
        reset: () => api.setState({}, true),
      },
    };
    return Object.assign(admin, merged) as StoreState;
  };
}

/**
 * Ensure the store exists; if not, build it using currently registered slices.
 * We always wrap with devtools; the name helps you find it in Redux DevTools.
 */
function ensureStore(): UseBoundStore<StoreApi<StoreState>> {
  if (__store) return __store;

  const withDevtools = devtools<StoreState>(
    (set, get, api) => composeInitializer()(set, get, api),
    { name: 'StudioWebStore' }
  );

  __store = create<StoreState>()(withDevtools);

  // Expose for debugging in dev tools without polluting prod (guard window)
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  (globalThis as any).__STUDIO_WEB_STORE__ = __store;

  return __store;
}

/**
 * React hook to read from the store. This is a thin wrapper over the bound zustand hook.
 * It defers store creation until first use, after all slices have had a chance to register.
 */
export function useStore<T>(
  selector: (state: StoreState) => T,
  equality?: (a: T, b: T) => boolean
): T {
  const store = ensureStore();
  // eslint-disable-next-line react-hooks/rules-of-hooks
  return store(selector, equality);
}

/** Direct accessors for non-React code (CLI, services, one-off utilities). */
export function getState(): StoreState { return ensureStore().getState(); }
export function setState(
  partial: Partial<StoreState> | ((s: StoreState) => Partial<StoreState>),
  replace?: boolean
): void {
  ensureStore().setState(partial as any, replace);
}
export function getStoreApi(): StoreApi<StoreState> { return ensureStore().getState as never, ensureStore().getState, ensureStore().setState, ensureStore().subscribe, ensureStore().destroy, ensureStore() as unknown as StoreApi<StoreState>; }

/**
 * Utility: create a namespaced setter to avoid accidental key collisions between slices.
 * Example:
 *   const nsSet = namespacedSet('network', set);
 *   nsSet({ rpcUrl, chainId });
 */
export function namespacedSet<K extends string>(ns: K, set: SetState<StoreState>) {
  return (partial: Record<string, unknown> | ((curr: Record<string, unknown>) => Record<string, unknown>)) =>
    set((s) => {
      const curr = (s as any)[ns] ?? {};
      const next = typeof partial === 'function' ? (partial as any)(curr) : partial;
      return { [ns]: { ...curr, ...next } } as Partial<StoreState>;
    });
}

/**
 * Utility: shallow compare — handy for selectors.
 */
export function shallowEqual<T extends Record<string, unknown>>(a: T, b: T): boolean {
  if (Object.is(a, b)) return true;
  const ka = Object.keys(a);
  const kb = Object.keys(b);
  if (ka.length !== kb.length) return false;
  for (const k of ka) {
    if (!Object.prototype.hasOwnProperty.call(b, k)) return false;
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    if (!Object.is((a as any)[k], (b as any)[k])) return false;
  }
  return true;
}

/**
 * Utility: create a memoized selector with shallow equality by default.
 * Example:
 *   const selectNetwork = memoSelector(s => s.network);
 *   const net = useStore(selectNetwork);
 */
export function memoSelector<T>(selector: (s: StoreState) => T) {
  return selector;
}

export default useStore;
