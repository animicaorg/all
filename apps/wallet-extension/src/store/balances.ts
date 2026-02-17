import { useSyncExternalStore } from 'react';
import * as balancesService from '../services/balances';

export type BalanceStatus = 'ok' | 'loading' | 'error';

export interface BalanceState {
  status: BalanceStatus;
  valueAtomic?: string;
  formatted?: string;
  error?: { code?: number | string; message: string };
  updatedAt: number;
}

interface BalanceContext {
  chainId: number;
  rpcUrl: string;
}

interface BalancesState {
  balancesByKey: Record<string, BalanceState | undefined>;
  keyByAddress: Record<string, string | undefined>;
}

interface BalancesActions {
  refreshBalance: (address: string, force?: boolean, context?: BalanceContext) => Promise<void>;
  refreshBalances: (addresses: string[], force?: boolean, context?: BalanceContext) => Promise<void>;
  getBalanceState: (address: string, context?: BalanceContext) => BalanceState | undefined;
}

type BalancesStore = BalancesState & BalancesActions;

type Listener = () => void;

const MIN_REFETCH_MS = 5000;
const ERROR_LOG_COOLDOWN_MS = 30000;
const MAX_CONCURRENT_FETCHES = 4;

const inFlightByKey = new Map<string, Promise<void>>();
const requestIdByKey = new Map<string, number>();
const lastErrorLogByKey = new Map<string, { message: string; at: number }>();
const listeners = new Set<Listener>();

let networkContextCache: { value: BalanceContext; at: number } | null = null;

const state: BalancesState = {
  balancesByKey: {},
  keyByAddress: {},
};

function normalizeAddress(address: string): string {
  return address.trim().toLowerCase();
}

function buildBalanceKey(address: string, context: BalanceContext): string {
  return `${context.chainId}:${context.rpcUrl}:${normalizeAddress(address)}`;
}

async function resolveContext(input?: BalanceContext): Promise<BalanceContext> {
  if (input) return input;
  if (networkContextCache && Date.now() - networkContextCache.at < 5000) return networkContextCache.value;
  const network = await chrome.runtime.sendMessage({ method: 'wallet_getCurrentNetwork' });
  const context = {
    chainId: Number(network?.chainId ?? 0),
    rpcUrl: String(network?.effectiveRpcUrl ?? ''),
  };
  networkContextCache = { value: context, at: Date.now() };
  return context;
}

function emit(): void {
  listeners.forEach((listener) => listener());
}

function setBalanceState(key: string, next: BalanceState, address: string): void {
  state.balancesByKey = { ...state.balancesByKey, [key]: next };
  state.keyByAddress = { ...state.keyByAddress, [normalizeAddress(address)]: key };
  emit();
}

function logFetchError(key: string, error: unknown, requestId: number): void {
  const message = error instanceof Error ? error.message : String(error);
  const now = Date.now();
  const last = lastErrorLogByKey.get(key);
  if (last && last.message === message && now - last.at < ERROR_LOG_COOLDOWN_MS) {
    return;
  }
  lastErrorLogByKey.set(key, { message, at: now });
  console.error('[balances] balance.fetch.error', { key, requestId, message });
}

function shouldSkipRecentFetch(existing: BalanceState | undefined, force: boolean): boolean {
  if (force) return false;
  if (!existing?.updatedAt) return false;
  return Date.now() - existing.updatedAt < MIN_REFETCH_MS;
}

async function refreshBalanceInternal(address: string, force: boolean, context: BalanceContext): Promise<void> {
  if (!address) return;
  const normalizedAddress = normalizeAddress(address);
  const key = buildBalanceKey(normalizedAddress, context);
  const existing = state.balancesByKey[key];
  if (shouldSkipRecentFetch(existing, force)) return;

  const inFlight = inFlightByKey.get(key);
  if (inFlight) return inFlight;

  const requestId = (requestIdByKey.get(key) ?? 0) + 1;
  requestIdByKey.set(key, requestId);

  const loadingState: BalanceState = {
    status: 'loading',
    updatedAt: Date.now(),
    ...(existing?.status === 'ok' ? { valueAtomic: existing.valueAtomic, formatted: existing.formatted } : {}),
  };
  setBalanceState(key, loadingState, normalizedAddress);

  const promise = (async () => {
    try {
      const balance = await balancesService.getBalance(normalizedAddress);
      if (requestIdByKey.get(key) !== requestId) return;

      setBalanceState(key, {
        status: 'ok',
        valueAtomic: balance.toString(),
        formatted: balancesService.formatANM(balance),
        updatedAt: Date.now(),
      }, normalizedAddress);
    } catch (error) {
      if (requestIdByKey.get(key) !== requestId) return;
      logFetchError(key, error, requestId);
      setBalanceState(key, {
        status: 'error',
        updatedAt: Date.now(),
        valueAtomic: existing?.valueAtomic,
        formatted: existing?.formatted,
        error: { message: error instanceof Error ? error.message : String(error) },
      }, normalizedAddress);
    } finally {
      inFlightByKey.delete(key);
    }
  })();

  inFlightByKey.set(key, promise);
  return promise;
}

async function runWithLimit<T>(items: T[], limit: number, worker: (item: T) => Promise<void>): Promise<void> {
  let idx = 0;
  const workers = Array.from({ length: Math.min(limit, items.length) }, async () => {
    while (idx < items.length) {
      const current = items[idx++];
      await worker(current);
    }
  });
  await Promise.all(workers);
}

const actions: BalancesActions = {
  async refreshBalance(address: string, force = false, context?: BalanceContext): Promise<void> {
    const resolvedContext = await resolveContext(context);
    await refreshBalanceInternal(address, force, resolvedContext);
  },

  async refreshBalances(addresses: string[], force = false, context?: BalanceContext): Promise<void> {
    const resolvedContext = await resolveContext(context);
    const unique = Array.from(new Set(addresses.map(normalizeAddress).filter(Boolean)));
    await runWithLimit(unique, MAX_CONCURRENT_FETCHES, async (address) => {
      await refreshBalanceInternal(address, force, resolvedContext);
    });
  },

  getBalanceState(address: string, context?: BalanceContext): BalanceState | undefined {
    const normalizedAddress = normalizeAddress(address);
    if (context) return state.balancesByKey[buildBalanceKey(normalizedAddress, context)];
    const latestKey = state.keyByAddress[normalizedAddress];
    return latestKey ? state.balancesByKey[latestKey] : undefined;
  },
};

export function getBalancesStoreSnapshot(): BalancesStore {
  return {
    ...state,
    ...actions,
  };
}

export function subscribeBalancesStore(listener: Listener): () => void {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

export function useBalancesStore<T>(selector: (store: BalancesStore) => T): T {
  return useSyncExternalStore(
    subscribeBalancesStore,
    () => selector(getBalancesStoreSnapshot()),
    () => selector(getBalancesStoreSnapshot())
  );
}

export const balancesStoreActions = actions;

// Short changelog: added for deterministic unit tests around race/cache behavior.
export function __resetBalancesStoreForTests(): void {
  state.balancesByKey = {};
  state.keyByAddress = {};
  inFlightByKey.clear();
  requestIdByKey.clear();
  lastErrorLogByKey.clear();
  networkContextCache = null;
}
