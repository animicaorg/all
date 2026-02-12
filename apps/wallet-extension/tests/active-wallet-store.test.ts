import { beforeEach, describe, expect, it, vi } from 'vitest';

async function flushMicrotasks(): Promise<void> {
  await Promise.resolve();
  await Promise.resolve();
}

const getActiveWalletMock = vi.fn();
const setActiveWalletMock = vi.fn();
let listener: ((walletId: string) => void) | null = null;

vi.mock('../src/services/activeWallet', () => ({
  getActiveWallet: (...args: any[]) => getActiveWalletMock(...args),
  setActiveWallet: (...args: any[]) => setActiveWalletMock(...args),
  onActiveWalletChanged: (cb: (walletId: string) => void) => {
    listener = cb;
    return () => {
      listener = null;
    };
  },
}));

import { activeWalletStoreActions, getActiveWalletStoreSnapshot } from '../src/store/activeWallet';

describe('active wallet store', () => {
  beforeEach(() => {
    getActiveWalletMock.mockReset();
    setActiveWalletMock.mockReset();
    listener = null;
  });

  it('switchActiveWallet persists and hydrates active wallet', async () => {
    setActiveWalletMock.mockResolvedValue({ success: true });
    getActiveWalletMock.mockResolvedValue({ address: 'anim1new', label: 'New' });

    await activeWalletStoreActions.switchActiveWallet('anim1new');

    expect(setActiveWalletMock).toHaveBeenCalledWith('anim1new');
    expect(getActiveWalletStoreSnapshot().activeWallet?.address).toBe('anim1new');
  });

  it('notifies and refreshes on active wallet changed event', async () => {
    getActiveWalletMock.mockResolvedValue({ address: 'anim1first', label: 'First' });
    await activeWalletStoreActions.hydrateActiveWallet();

    const unsubscribe = activeWalletStoreActions.listenForActiveWalletChanges();

    getActiveWalletMock.mockResolvedValue({ address: 'anim1second', label: 'Second' });
    listener?.('anim1second');

    await flushMicrotasks();
    expect(getActiveWalletStoreSnapshot().activeWallet?.address).toBe('anim1second');
    unsubscribe();
  });
});
