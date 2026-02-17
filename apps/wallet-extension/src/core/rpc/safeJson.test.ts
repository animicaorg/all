import { describe, expect, it } from 'vitest';
import { stringifySafe } from './safeJson';

describe('stringifySafe', () => {
  it('serializes nested bigint and byte arrays safely', () => {
    const payload = {
      amount: 15n,
      nested: [{ fee: 2n, raw: new Uint8Array([0xab, 0xcd]) }],
    };

    const out = stringifySafe(payload);
    expect(out).toBe('{"amount":"15","nested":[{"fee":"2","raw":"0xabcd"}]}');
    expect(() => JSON.stringify(payload)).toThrow();
  });
});
