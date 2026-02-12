import { describe, it, expect, vi, beforeEach } from 'vitest';

vi.mock('../src/services/balances', () => ({
  getBalance: vi.fn(),
}));

import { balancesStoreActions, getBalancesStoreSnapshot } from '../src/store/balances';
import { getBalance } from '../src/services/balances';

const mockedGetBalance = vi.mocked(getBalance);

describe('balances store', () => {
  beforeEach(() => {
    mockedGetBalance.mockReset();
  });

  it('deduplicates in-flight requests for the same address', async () => {
    let resolveBalance: ((value: bigint) => void) | null = null;
    mockedGetBalance.mockImplementationOnce(() => new Promise(resolve => {
      resolveBalance = resolve;
    }));

    const address = 'anim1dedupe';
    const first = balancesStoreActions.refreshBalance(address, true);
    const second = balancesStoreActions.refreshBalance(address, true);

    expect(mockedGetBalance).toHaveBeenCalledTimes(1);

    resolveBalance?.(42n);
    await Promise.all([first, second]);

    const snapshot = getBalancesStoreSnapshot();
    expect(snapshot.balancesByAddress[address]).toBe(42n);
    expect(snapshot.loadingByAddress[address]).toBe(false);
  });

  it('skips refetch when last fetch is recent unless forced', async () => {
    const address = 'anim1recent';

    mockedGetBalance.mockResolvedValueOnce(100n);
    await balancesStoreActions.refreshBalance(address, true);

    mockedGetBalance.mockResolvedValueOnce(999n);
    await balancesStoreActions.refreshBalance(address, false);

    expect(mockedGetBalance).toHaveBeenCalledTimes(1);

    await balancesStoreActions.refreshBalance(address, true);
    expect(mockedGetBalance).toHaveBeenCalledTimes(2);
  });
});
