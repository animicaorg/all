import { describe, expect, it } from 'vitest';
import { stringifySafe, stringifyForStorage, toJsonSafe } from './safeJson';

describe('stringifySafe (RPC encoding)', () => {
  it('serializes nested bigint and byte arrays safely', () => {
    const payload = {
      amount: 15n,
      nested: [{ fee: 2n, raw: new Uint8Array([0xab, 0xcd]) }],
    };

    const out = stringifySafe(payload);
    expect(out).toBe('{"amount":"15","nested":[{"fee":"2","raw":"0xabcd"}]}');
    expect(() => JSON.stringify(payload)).toThrow();
  });

  it('handles deeply nested structures', () => {
    const payload = {
      a: { b: { c: { d: 12345678901234567890n } } },
      list: [1n, 2n, [3n, 4n]],
    };
    const out = JSON.parse(stringifySafe(payload));
    expect(out.a.b.c.d).toBe('12345678901234567890');
    expect(out.list).toEqual(['1', '2', ['3', '4']]);
  });

  it('passes through plain numbers, strings, booleans, null', () => {
    const out = JSON.parse(stringifySafe({ n: 42, s: 'x', b: true, z: null, u: undefined }));
    expect(out).toEqual({ n: 42, s: 'x', b: true, z: null });
  });

  it('handles ArrayBuffer and typed array views', () => {
    const buf = new ArrayBuffer(3);
    new Uint8Array(buf).set([1, 2, 3]);
    const out = JSON.parse(stringifySafe({ buf, view: new Uint8Array(buf) }));
    expect(out.buf).toBe('0x010203');
    expect(out.view).toBe('0x010203');
  });

  it('handles Map and Set', () => {
    const m = new Map<string, unknown>([
      ['a', 1n],
      ['b', new Uint8Array([0x10])],
    ]);
    const s = new Set([1n, 'x', new Uint8Array([0xff])]);
    const out = JSON.parse(stringifySafe({ m, s }));
    expect(out.m).toEqual({ a: '1', b: '0x10' });
    expect(out.s).toEqual(['1', 'x', '0xff']);
  });

  it('skips non-finite numbers (no JSON crash)', () => {
    const out = JSON.parse(stringifySafe({ inf: Number.POSITIVE_INFINITY, nan: Number.NaN }));
    expect(out.inf).toBeNull();
    expect(out.nan).toBeNull();
  });
});

describe('stringifyForStorage (vault-safe encoding)', () => {
  it('preserves Uint8Array as numeric-keyed object so coerceBytes can rebuild it', () => {
    const out = JSON.parse(stringifyForStorage({ key: new Uint8Array([1, 2, 255]) }));
    expect(out.key).toEqual({ 0: 1, 1: 2, 2: 255 });
  });

  it('still serializes bigint as string and never throws', () => {
    expect(() => stringifyForStorage({ fee: 10n, gas: 21000n, body: { value: 1_000_000_000n } })).not.toThrow();
    const out = JSON.parse(stringifyForStorage({ fee: 10n, gas: 21000n, body: { value: 1_000_000_000n } }));
    expect(out.fee).toBe('10');
    expect(out.gas).toBe('21000');
    expect(out.body.value).toBe('1000000000');
  });
});

describe('toJsonSafe', () => {
  it('is idempotent on already-safe inputs', () => {
    const input = { a: 'x', n: 1, arr: [1, 2, 3] };
    expect(toJsonSafe(input)).toEqual(input);
    expect(toJsonSafe(toJsonSafe(input))).toEqual(input);
  });

  it('does not mutate the input', () => {
    const input = { amount: 1n, raw: new Uint8Array([1]) };
    toJsonSafe(input);
    expect(typeof input.amount).toBe('bigint');
    expect(input.raw).toBeInstanceOf(Uint8Array);
  });
});
