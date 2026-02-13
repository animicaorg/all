import { describe, expect, it } from 'vitest';

import { buildJsonRpcRequest, RpcClient } from '../src/core/rpc/client';

describe('tx.sendRawTransaction request shape', () => {
  it('builds JSON-RPC payload using named params with rawTx', () => {
    const payload = buildJsonRpcRequest(
      'tx.sendRawTransaction',
      { rawTx: '0xdeadbeef' },
      123,
    );

    expect(payload).toEqual({
      jsonrpc: '2.0',
      id: 123,
      method: 'tx.sendRawTransaction',
      params: { rawTx: '0xdeadbeef' },
    });
  });

  it('validates that rawTx is 0x-prefixed even-length hex', async () => {
    const client = new RpcClient(['http://localhost:8545/rpc']);

    await expect(client.sendRawTransaction('not-hex')).rejects.toThrow(/0x-prefixed hex/);
    await expect(client.sendRawTransaction('deadbeef')).rejects.toThrow(/0x-prefixed hex/);
    await expect(client.sendRawTransaction('0xabc')).rejects.toThrow(/even/);
  });

  it('shows canonical and fallback schema examples', () => {
    const canonical = buildJsonRpcRequest('tx.sendRawTransaction', { rawTx: '0xabcd1234' }, 1);
    const fallback = buildJsonRpcRequest('tx.sendRawTransaction', ['0xabcd1234'], 2);

    expect(canonical.params).toEqual({ rawTx: '0xabcd1234' });
    expect(fallback.params).toEqual(['0xabcd1234']);
  });
});
