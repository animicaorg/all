import { describe, expect, it } from 'vitest';

import { buildJsonRpcRequest } from '../src/core/rpc/client';

describe('tx.sendRawTransaction request shape', () => {
  it('builds JSON-RPC payload using object params with rawTx', () => {
    const payload = buildJsonRpcRequest(
      'tx.sendRawTransaction',
      { rawTx: '0xdeadbeef' },
      123,
    );

    expect(payload).toEqual({
      jsonrpc: '2.0',
      id: 123,
      method: 'tx.sendRawTransaction',
      params: {
        rawTx: '0xdeadbeef',
      },
    });
  });

  it('prints a sample request payload for dev debugging', () => {
    const sample = buildJsonRpcRequest(
      'tx.sendRawTransaction',
      { rawTx: '0x01020304' },
      1,
    );

    // Intentionally logged for manual verification in dev/test runs.
    console.log('[dev] sample tx.sendRawTransaction request:', JSON.stringify(sample));
    expect(sample.params).toEqual({ rawTx: '0x01020304' });
  });
});
