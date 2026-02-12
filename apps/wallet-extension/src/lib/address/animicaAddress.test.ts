import { bech32m } from 'bech32';
import { describe, expect, it } from 'vitest';
import { decodeAnimAddress, encodeAnimAddress } from './animicaAddress';

describe('animicaAddress', () => {
  const payload = Uint8Array.from(Array.from({ length: 34 }, (_, idx) => idx + 1));

  it('encodes and decodes v1 address', () => {
    const address = encodeAnimAddress('anim', 1, payload);
    const decoded = decodeAnimAddress(address);

    expect(decoded.version).toBe(1);
    expect(decoded.hrp).toBe('anim');
    expect(Array.from(decoded.payload)).toEqual(Array.from(payload));
  });

  it('accepts v2 address', () => {
    const address = encodeAnimAddress('anim', 2, payload);
    const decoded = decodeAnimAddress(address);

    expect(decoded.version).toBe(2);
  });

  it('rejects unknown versions', () => {
    const bad = bech32m.encode('anim', [3, ...bech32m.toWords(payload)]);
    expect(() => decodeAnimAddress(bad)).toThrow('Unsupported address version: 3');
  });
});
