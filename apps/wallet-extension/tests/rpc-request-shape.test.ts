import { describe, expect, it } from 'vitest';

import { buildJsonRpcRequest, RpcClient } from '../src/core/rpc/client';

describe('tx.sendRawTransaction request shape', () => {
  it('normalizes named rawTx params into positional array for tx broadcast', () => {
    const payload = buildJsonRpcRequest(
      'tx.sendRawTransaction',
      { rawTx: '0xdeadbeef' },
      123,
    );

    expect(payload).toEqual({
      jsonrpc: '2.0',
      id: 123,
      method: 'tx.sendRawTransaction',
      params: ['0xdeadbeef'],
    });
  });

  it('validates malformed rawTx before network calls', async () => {
    const client = new RpcClient(['http://localhost:8545/rpc']);

    await expect(client.sendRawTransaction('not-hex')).rejects.toThrow(/must start with 0x/);
    await expect(client.sendRawTransaction('0xzz')).rejects.toThrow(/must be hex/);
    await expect(client.sendRawTransaction('0x00')).rejects.toThrow(/too short/);
  });

  it('keeps read-only calls untouched', () => {
    const payload = buildJsonRpcRequest('chain.getHead', { foo: 'bar' } as any, 2);
    expect(payload.params).toEqual({ foo: 'bar' });
  });
});
