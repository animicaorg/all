import { useSyncExternalStore } from 'react';
import * as balancesService from '../services/balances';

interface BalancesState {
  balancesByAddress: Record<string, bigint | undefined>;
  lastUpdatedByAddress: Record<string, number | undefined>;
  loadingByAddress: Record<string, boolean | undefined>;
  errorByAddress: Record<string, string | null | undefined>;
}

interface BalancesActions {
  refreshBalance: (address: string, force?: boolean) => Promise<void>;
  refreshBalances: (addresses: string[], force?: boolean) => Promise<void>;
}

type BalancesStore = BalancesState & BalancesActions;

type Listener = () => void;

const MIN_REFETCH_MS = 5000;
const ERROR_LOG_COOLDOWN_MS = 30000;

const inFlightByAddress = new Map<string, Promise<void>>();
const listeners = new Set<Listener>();
const lastErrorLogByAddress = new Map<string, { message: string; at: number }>();

const state: BalancesState = {
  balancesByAddress: {},
  lastUpdatedByAddress: {},
  loadingByAddress: {},
  errorByAddress: {},
};

function emit(): void {
  listeners.forEach(listener => listener());
}

function setPartial(partial: Partial<BalancesState>): void {
  Object.assign(state, partial);
  emit();
}

function shouldSkipRecentFetch(address: string, force: boolean): boolean {
  if (force) {
    return false;
  }

  const lastUpdated = state.lastUpdatedByAddress[address];
  if (!lastUpdated) {
    return false;
  }

  return Date.now() - lastUpdated < MIN_REFETCH_MS;
}

function logFetchError(address: string, error: unknown): void {
  const message = error instanceof Error ? error.message : String(error);
  const now = Date.now();
  const last = lastErrorLogByAddress.get(address);

  if (last && last.message === message && now - last.at < ERROR_LOG_COOLDOWN_MS) {
    return;
  }

  lastErrorLogByAddress.set(address, { message, at: now });
  console.error(`[balances] ${address}: ${message}`);
}

async function refreshBalanceInternal(address: string, force: boolean): Promise<void> {
  if (!address) {
    return;
  }

  if (shouldSkipRecentFetch(address, force)) {
    return;
  }

  const existing = inFlightByAddress.get(address);
  if (existing) {
    return existing;
  }

  const promise = (async () => {
    setPartial({
      loadingByAddress: {
        ...state.loadingByAddress,
        [address]: true,
      },
    });

    try {
      const balance = await balancesService.getBalance(address);
      setPartial({
        balancesByAddress: {
          ...state.balancesByAddress,
          [address]: balance,
        },
        lastUpdatedByAddress: {
          ...state.lastUpdatedByAddress,
          [address]: Date.now(),
        },
        errorByAddress: {
          ...state.errorByAddress,
          [address]: null,
        },
      });
    } catch (error) {
      const errorMsg = error instanceof Error ? error.message : 'Unknown error';
      logFetchError(address, error);
      setPartial({
        errorByAddress: {
          ...state.errorByAddress,
          [address]: errorMsg, // Store actual error message instead of generic 'unavailable'
        },
      });
    } finally {
      setPartial({
        loadingByAddress: {
          ...state.loadingByAddress,
          [address]: false,
        },
      });
      inFlightByAddress.delete(address);
    }
  })();

  inFlightByAddress.set(address, promise);
  return promise;
}

const actions: BalancesActions = {
  async refreshBalance(address: string, force = false): Promise<void> {
    await refreshBalanceInternal(address, force);
  },

  async refreshBalances(addresses: string[], force = false): Promise<void> {
    const unique = Array.from(new Set(addresses.filter(Boolean)));
    await Promise.all(unique.map(address => refreshBalanceInternal(address, force)));
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
