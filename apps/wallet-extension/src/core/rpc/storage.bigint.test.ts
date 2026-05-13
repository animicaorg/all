/**
 * Regression: BigInt values inside the signed transaction envelope (value,
 * fee, gas_limit) used to crash JSON.stringify when the wallet persisted a
 * pending tx into the encrypted vault with the literal error:
 *
 *   "Do not know how to serialize a BigInt"
 *
 * The wallet now routes vault persistence through stringifyForStorage and
 * pre-converts the pending tx payload via toJsonSafe(..., 'storage') in the
 * background. This test reproduces the exact shape that came out of
 * buildAndSignTransaction and confirms it stringifies without throwing and
 * survives a roundtrip through JSON.parse + coerceBytes-like recovery.
 */

import { describe, expect, it } from 'vitest';
import { stringifyForStorage, toJsonSafe } from './safeJson';

function rebuildBytesFromNumericObject(value: unknown): Uint8Array | null {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return null;
  const entries = Object.entries(value as Record<string, unknown>)
    .map(([k, v]) => [Number(k), v] as const)
    .sort((a, b) => a[0] - b[0]);
  if (!entries.length || entries[0][0] !== 0) return null;
  const out = new Uint8Array(entries.length);
  for (let i = 0; i < entries.length; i += 1) {
    const [pos, b] = entries[i];
    if (pos !== i) return null;
    if (typeof b !== 'number' || b < 0 || b > 255) return null;
    out[i] = b;
  }
  return out;
}

describe('vault storage tolerates bigint and Uint8Array fields', () => {
  const pendingTx = {
    txid: '0xabc',
    unsignedHash: '0xabc',
    signedTx: {
      tx: {
        version: 1,
        chain_id: 1,
        nonce: 5,
        from_addr: new Uint8Array([0x01, 0x02, 0x03]),
        to_addr: new Uint8Array([0x04, 0x05, 0x06]),
        value: 1_000_000_000_000_000_000n,
        fee: 21_000_000n,
        gas_limit: 21000n,
        data: new Uint8Array(),
        memo: '',
        timestamp: 1700000000,
        kind: 0,
      },
      sigs: [{
        alg: 1,
        pubkey: new Uint8Array([0xaa, 0xbb]),
        sig: new Uint8Array([0xcc, 0xdd]),
      }],
    },
    status: 'submitted',
    submittedAt: 1700000000000,
  };

  it('does not throw "Do not know how to serialize a BigInt"', () => {
    expect(() => JSON.stringify(pendingTx)).toThrow();
    expect(() => stringifyForStorage(pendingTx)).not.toThrow();
  });

  it('bigint roundtrips as decimal string', () => {
    const out = JSON.parse(stringifyForStorage(pendingTx));
    expect(out.signedTx.tx.value).toBe('1000000000000000000');
    expect(out.signedTx.tx.fee).toBe('21000000');
    expect(out.signedTx.tx.gas_limit).toBe('21000');
  });

  it('Uint8Array survives a roundtrip via numeric-keyed object form', () => {
    const out = JSON.parse(stringifyForStorage(pendingTx));
    const fromAddr = rebuildBytesFromNumericObject(out.signedTx.tx.from_addr);
    expect(fromAddr).toEqual(new Uint8Array([0x01, 0x02, 0x03]));
    const pubkey = rebuildBytesFromNumericObject(out.signedTx.sigs[0].pubkey);
    expect(pubkey).toEqual(new Uint8Array([0xaa, 0xbb]));
  });

  it('toJsonSafe(storage) does not mutate the source pendingTx', () => {
    toJsonSafe(pendingTx, 'storage');
    expect(typeof pendingTx.signedTx.tx.value).toBe('bigint');
    expect(pendingTx.signedTx.tx.from_addr).toBeInstanceOf(Uint8Array);
  });
});
