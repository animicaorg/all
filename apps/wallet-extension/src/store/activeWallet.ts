import { useSyncExternalStore } from 'react';
import type { Account } from '../types/wallet';
import { getActiveWallet, onActiveWalletChanged, setActiveWallet } from '../services/activeWallet';

interface ActiveWalletState {
  activeWallet: Account | null;
  loading: boolean;
  error: string | null;
}

interface ActiveWalletActions {
  hydrateActiveWallet: () => Promise<void>;
  switchActiveWallet: (walletId: string) => Promise<void>;
  listenForActiveWalletChanges: () => () => void;
}

type ActiveWalletStore = ActiveWalletState & ActiveWalletActions;
type Listener = () => void;

const listeners = new Set<Listener>();

const state: ActiveWalletState = {
  activeWallet: null,
  loading: false,
  error: null,
};

function emit(): void {
  listeners.forEach(listener => listener());
}

function setPartial(partial: Partial<ActiveWalletState>): void {
  Object.assign(state, partial);
  emit();
}

const actions: ActiveWalletActions = {
  async hydrateActiveWallet(): Promise<void> {
    setPartial({ loading: true, error: null });
    try {
      const activeWallet = await getActiveWallet();
      setPartial({ activeWallet, loading: false, error: null });
    } catch (error: any) {
      setPartial({ loading: false, error: error?.message || 'Failed to load active wallet' });
    }
  },

  async switchActiveWallet(walletId: string): Promise<void> {
    await setActiveWallet(walletId);
    await actions.hydrateActiveWallet();
  },

  listenForActiveWalletChanges(): () => void {
    return onActiveWalletChanged(async () => {
      await actions.hydrateActiveWallet();
    });
  },
};

export function subscribeActiveWalletStore(listener: Listener): () => void {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

export function getActiveWalletStoreSnapshot(): ActiveWalletStore {
  return {
    ...state,
    ...actions,
  };
}

export function useActiveWalletStore<T>(selector: (store: ActiveWalletStore) => T): T {
  return useSyncExternalStore(
    subscribeActiveWalletStore,
    () => selector(getActiveWalletStoreSnapshot()),
    () => selector(getActiveWalletStoreSnapshot())
  );
}

export const activeWalletStoreActions = actions;
