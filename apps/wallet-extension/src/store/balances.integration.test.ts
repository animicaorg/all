import { beforeEach, describe, expect, it, vi } from 'vitest';
import { __resetBalancesStoreForTests, balancesStoreActions, getBalancesStoreSnapshot } from './balances';

const rpcUrl = 'https://rpc.example/rpc';
const chainId = 1337;

describe('balances store integration', () => {
  beforeEach(() => {
    __resetBalancesStoreForTests();

    const values: Record<string, string> = {
      anim1aaa: '1000000000',
      anim1bbb: '2000000000',
      anim1ccc: '3000000000',
    };

    (globalThis as any).chrome = {
      runtime: {
        sendMessage: vi.fn(async (msg: any) => {
          if (msg.method === 'wallet_getCurrentNetwork') {
            return { chainId, effectiveRpcUrl: rpcUrl };
          }
          if (msg.method === 'wallet_getBalance') {
            const address = String(msg?.params?.address || '').toLowerCase();
            return { confirmed: values[address], available: values[address] };
          }
          throw new Error(`Unexpected message ${msg.method}`);
        }),
      },
    };
  });

  it('keeps distinct balances per wallet and survives reordering', async () => {
    const addresses = ['anim1aaa', 'anim1bbb', 'anim1ccc'];
    await balancesStoreActions.refreshBalances(addresses, true);

    const first = getBalancesStoreSnapshot();
    expect(first.getBalanceState('anim1aaa')?.valueAtomic).toBe('1000000000');
    expect(first.getBalanceState('anim1bbb')?.valueAtomic).toBe('2000000000');
    expect(first.getBalanceState('anim1ccc')?.valueAtomic).toBe('3000000000');

    await balancesStoreActions.refreshBalances(['anim1ccc', 'anim1aaa', 'anim1bbb'], true);

    const second = getBalancesStoreSnapshot();
    expect(second.getBalanceState('anim1aaa')?.valueAtomic).toBe('1000000000');
    expect(second.getBalanceState('anim1bbb')?.valueAtomic).toBe('2000000000');
    expect(second.getBalanceState('anim1ccc')?.valueAtomic).toBe('3000000000');
  });
});
