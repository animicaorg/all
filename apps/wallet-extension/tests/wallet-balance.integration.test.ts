import { beforeEach, describe, expect, it, vi } from 'vitest';

async function flushMicrotasks(): Promise<void> {
  await Promise.resolve();
  await Promise.resolve();
}

const getActiveWalletMock = vi.fn();
const setActiveWalletMock = vi.fn();
const getBalanceMock = vi.fn();
let activeListener: ((walletId: string) => void) | null = null;

vi.mock('../src/services/activeWallet', () => ({
  getActiveWallet: (...args: any[]) => getActiveWalletMock(...args),
  setActiveWallet: (...args: any[]) => setActiveWalletMock(...args),
  onActiveWalletChanged: (cb: (walletId: string) => void) => {
    activeListener = cb;
    return () => {
      activeListener = null;
    };
  },
}));

vi.mock('../src/services/balances', () => ({
  getBalance: (...args: any[]) => getBalanceMock(...args),
}));

import { activeWalletStoreActions, getActiveWalletStoreSnapshot } from '../src/store/activeWallet';
import { balancesStoreActions, getBalancesStoreSnapshot } from '../src/store/balances';

describe('wallet + balance integration', () => {
  beforeEach(() => {
    getActiveWalletMock.mockReset();
    setActiveWalletMock.mockReset();
    getBalanceMock.mockReset();
    activeListener = null;
  });

  it('switches active wallet and refreshes balance with wallet-specific responses', async () => {
    getActiveWalletMock.mockResolvedValueOnce({ address: 'anim1A', label: 'Wallet A' });
    getBalanceMock.mockImplementation(async (address: string) => (address === 'anim1A' ? 50n : 0n));

    await activeWalletStoreActions.hydrateActiveWallet();
    const unsubscribe = activeWalletStoreActions.listenForActiveWalletChanges();
    await balancesStoreActions.refreshBalance('anim1A', true);

    expect(getActiveWalletStoreSnapshot().activeWallet?.address).toBe('anim1A');
    expect(getBalancesStoreSnapshot().balancesByAddress.anim1A).toBe(50n);

    setActiveWalletMock.mockResolvedValue({ success: true });
    getActiveWalletMock.mockResolvedValueOnce({ address: 'anim1B', label: 'Wallet B' });
    getBalanceMock.mockImplementation(async (address: string) => (address === 'anim1A' ? 50n : 0n));

    await activeWalletStoreActions.switchActiveWallet('anim1B');
    await balancesStoreActions.refreshBalance('anim1B', true);

    expect(getActiveWalletStoreSnapshot().activeWallet?.address).toBe('anim1B');
    expect(getBalancesStoreSnapshot().balancesByAddress.anim1B).toBe(0n);

    getActiveWalletMock.mockResolvedValueOnce({ address: 'anim1A', label: 'Wallet A' });
    activeListener?.('anim1A');
    await flushMicrotasks();
    expect(getActiveWalletStoreSnapshot().activeWallet?.address).toBe('anim1A');
    await balancesStoreActions.refreshBalance('anim1A', true);
    expect(getBalancesStoreSnapshot().balancesByAddress.anim1A).toBe(50n);
    unsubscribe();
  });
});
