import { describe, expect, it } from 'vitest';

import { buildJsonRpcRequest, RpcClient } from '../src/core/rpc/client';

describe('tx.sendRawTransaction request shape', () => {
  it('builds JSON-RPC payload using positional params with rawTx', () => {
    const payload = buildJsonRpcRequest(
      'tx.sendRawTransaction',
      ['0xdeadbeef'],
      123,
    );

    expect(payload).toEqual({
      jsonrpc: '2.0',
      id: 123,
      method: 'tx.sendRawTransaction',
      params: ['0xdeadbeef'],
    });
  });

  it('prints a sample request payload for dev debugging', () => {
    const sample = buildJsonRpcRequest(
      'tx.sendRawTransaction',
      ['0x01020304'],
      1,
    );

    // Intentionally logged for manual verification in dev/test runs.
    console.log('[dev] sample tx.sendRawTransaction request:', JSON.stringify(sample));
    expect(sample.params).toEqual(['0x01020304']);
  });

  it('validates that rawTx is a hex string', async () => {
    const client = new RpcClient(['http://localhost:8545/rpc']);

    // Should reject non-hex strings
    await expect(
      client.sendRawTransaction('not-hex')
    ).rejects.toThrow(/expected 0x-prefixed hex string/);

    // Should reject missing 0x prefix
    await expect(
      client.sendRawTransaction('deadbeef')
    ).rejects.toThrow(/expected 0x-prefixed hex string/);

    // Should reject empty string
    await expect(
      client.sendRawTransaction('')
    ).rejects.toThrow(/expected 0x-prefixed hex string/);
  });

  it('documents the node RPC signature for reference', () => {
    // NODE RPC SIGNATURE: rpc/methods/tx.py
    // 
    // @method("tx.sendRawTransaction", ...)
    // def tx_send_raw_transaction(rawTx: str) -> t.Any
    //
    // The dispatcher (rpc/jsonrpc.py) accepts params in two forms:
    //   1. Array:  params: ["0xabcd1234..."]        → binds to positional arg
    //   2. Object: params: { rawTx: "0xabcd1234..." }  → binds to keyword arg
    //
    // Extension must use array form to match CLI request format exactly.

    const arrayForm = buildJsonRpcRequest('tx.sendRawTransaction', ['0xabcd1234'], 1);
    expect(arrayForm.params).toEqual(['0xabcd1234']);

    console.log('[dev] CLI-compatible request form:');
    console.log('  Array form: ', JSON.stringify(arrayForm));
  });

  it('prevents common mistakes that cause -32602 errors', () => {
    const bad = buildJsonRpcRequest('tx.sendRawTransaction', { rawTx: '0xabcd1234' }, 1);
    expect(Array.isArray(bad.params)).toBe(false);
    console.log('[dev] WRONG (extension must use positional params):', JSON.stringify(bad));

    const good = buildJsonRpcRequest('tx.sendRawTransaction', ['0xabcd1234'], 2);
    expect(good.params).toEqual(['0xabcd1234']);
    console.log('[dev] CORRECT:', JSON.stringify(good));
  });
});
