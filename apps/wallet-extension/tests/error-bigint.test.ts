import { describe, expect, it } from 'vitest';

import { stringifySafe } from '../src/core/rpc/safeJson';
import { unwrap, type Result } from '../src/types/result';

describe('error path bigint safety', () => {
  it('unwrap on a bigint-bearing error does not throw "Do not know how to serialize a BigInt"', () => {
    const err: Result<number, { code: number; data: { gasPrice: bigint } }> = {
      ok: false,
      error: { code: -32012, data: { gasPrice: 12_345_678_901n } },
    };
    expect(() => unwrap(err)).toThrowError(/12345678901/);
  });

  it('unwrap preserves string errors verbatim', () => {
    const err: Result<number, string> = { ok: false, error: 'plain error' };
    expect(() => unwrap(err)).toThrowError('plain error');
  });

  it('stringifySafe round-trips deeply nested bigint structures used in RPC error data', () => {
    const data = {
      fee: { gasPrice: 1n, maxFee: 2n ** 64n, nested: { tip: 7n } },
      txValue: 9_999_999_999_999_999_999n,
    };
    const text = stringifySafe(data);
    // Decimal-string encoding keeps precision exactly.
    expect(text).toContain('"1"');
    expect(text).toContain('"9999999999999999999"');
    // Round-trip via JSON.parse should not crash and should preserve digits.
    const back = JSON.parse(text) as { fee: { gasPrice: string; maxFee: string } };
    expect(back.fee.gasPrice).toBe('1');
    expect(typeof back.fee.maxFee).toBe('string');
  });
});
