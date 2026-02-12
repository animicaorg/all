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

import { formatBalance, getBalance, parseBaseUnits } from '../src/services/balanceService';

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

  it('parses sample payload {result:"81000000000000000"}', () => {
    expect(parseBaseUnits('81000000000000000')).toBe(81000000000000000n);
  });

  it('parses sample payload {result:{balance:"123"}}', () => {
    expect(parseBaseUnits({ balance: '123' })).toBe(123n);
  });

  it('handles object-wrapped RPC response {"balance":"0x7b"}', async () => {
    getChainIdMock.mockResolvedValue(1);
    callMock.mockResolvedValue({ balance: '0x7b' }); // 123 in hex

    const balance = await getBalance('anim1testaddress', {
      rpcUrl: 'https://rpc.animica.io',
      chainId: 1,
    });

    expect(balance).toBe(123n);
  });

  it('handles direct string RPC response "0x7b"', async () => {
    getChainIdMock.mockResolvedValue(1);
    callMock.mockResolvedValue('0x7b'); // 123 in hex

    const balance = await getBalance('anim1testaddress', {
      rpcUrl: 'https://rpc.animica.io',
      chainId: 1,
    });

    expect(balance).toBe(123n);
  });

  it('surfaces rpc errors instead of returning 0', async () => {
    getChainIdMock.mockResolvedValue(1);
    callMock.mockRejectedValue(new Error('RPC -32010: failure'));

    await expect(getBalance('anim1testaddress', {
      rpcUrl: 'https://rpc.animica.io',
      chainId: 1,
    })).rejects.toThrow('RPC -32010: failure');
  });

  it('formats small balances without rounding to zero', () => {
    expect(formatBalance(1n)).toBe('0.000000001');
    expect(formatBalance(12345n)).toBe('0.000012345');
  });
});
