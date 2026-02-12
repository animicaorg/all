import { describe, expect, it } from 'vitest';

import { buildJsonRpcRequest, RpcClient } from '../src/core/rpc/client';

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
    //   1. Array:  params: ["0xabcd..."]        → binds to positional arg
    //   2. Object: params: { rawTx: "0x..." }  → binds to keyword arg
    //
    // We use object form for explicitness and type safety.

    const arrayForm = buildJsonRpcRequest('tx.sendRawTransaction', ['0xabcd'], 1);
    const objectForm = buildJsonRpcRequest('tx.sendRawTransaction', { rawTx: '0xabcd' }, 2);

    expect(arrayForm.params).toEqual(['0xabcd']);
    expect(objectForm.params).toEqual({ rawTx: '0xabcd' });

    console.log('[dev] Both forms are valid per node dispatcher:');
    console.log('  Array form: ', JSON.stringify(arrayForm));
    console.log('  Object form:', JSON.stringify(objectForm));
  });

  it('prevents common mistakes that cause -32602 errors', () => {
    // MISTAKE 1: Double-wrapping params
    const bad1 = buildJsonRpcRequest('tx.sendRawTransaction', { params: ['0x...'] }, 1);
    // Node sees: params.params which doesn't bind to rawTx arg
    expect(bad1.params).toEqual({ params: ['0x...'] });
    console.log('[dev] WRONG (double-wrapped):', JSON.stringify(bad1));

    // MISTAKE 2: Missing rawTx key
    const bad2 = buildJsonRpcRequest('tx.sendRawTransaction', { tx: '0x...' }, 2);
    // Node sees: tx= not rawTx=
    expect(bad2.params).toEqual({ tx: '0x...' });
    console.log('[dev] WRONG (wrong key):', JSON.stringify(bad2));

    // CORRECT
    const good = buildJsonRpcRequest('tx.sendRawTransaction', { rawTx: '0x...' }, 3);
    expect(good.params).toEqual({ rawTx: '0x...' });
    console.log('[dev] CORRECT:', JSON.stringify(good));
  });
});
