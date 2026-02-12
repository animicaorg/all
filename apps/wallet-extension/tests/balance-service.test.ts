import { describe, expect, it, vi } from 'vitest';

const callMock = vi.fn();
const getChainIdMock = vi.fn();

vi.mock('../src/core/rpc/client', () => ({
  RpcClient: class {
    call = callMock;
    getChainId = getChainIdMock;
  },
}));

vi.mock('../src/core/crypto/address', () => ({
  validateAddress: vi.fn(() => true),
}));

import { formatBalance, getBalance } from '../src/services/balanceService';

describe('balance service', () => {
  it('uses rpc url and returns on-chain balance', async () => {
    getChainIdMock.mockResolvedValue(1);
    callMock.mockResolvedValue('1234000000000');

    const balance = await getBalance('anim1testaddress', {
      rpcUrl: 'https://rpc.animica.io',
      chainId: 1,
    });

    expect(getChainIdMock).toHaveBeenCalledTimes(1);
    expect(callMock).toHaveBeenCalledWith('state.getBalance', [
      'anim1testaddress',
      'latest',
    ]);
    expect(balance).toBe(1234000000000n);
  });

  it('formats small balances without rounding to zero', () => {
    expect(formatBalance(1n)).toBe('0.000000001');
    expect(formatBalance(12345n)).toBe('0.000012345');
  });
});
