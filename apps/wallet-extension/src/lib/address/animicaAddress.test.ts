import { bech32m } from 'bech32';
import { describe, expect, it } from 'vitest';
import { decodeAnimAddress, encodeAnimAddress } from './animicaAddress';

describe('animicaAddress', () => {
  const payload = Uint8Array.from(Array.from({ length: 34 }, (_, idx) => idx + 1));

  it('encodes and decodes address (no version byte)', () => {
    const address = encodeAnimAddress('anim', payload);
    const decoded = decodeAnimAddress(address);

    expect(decoded.hrp).toBe('anim');
    expect(Array.from(decoded.payload)).toEqual(Array.from(payload));
  });

  it('roundtrips address correctly', () => {
    const address1 = encodeAnimAddress('anim', payload);
    const decoded = decodeAnimAddress(address1);
    const address2 = encodeAnimAddress(decoded.hrp, decoded.payload);
    
    expect(address1).toBe(address2);
  });

  it('rejects invalid payload length', () => {
    const shortPayload = new Uint8Array(10);
    expect(() => encodeAnimAddress('anim', shortPayload)).toThrow('Invalid payload length');
  });

  it('rejects invalid HRP', () => {
    const address = encodeAnimAddress('test', payload);
    expect(() => decodeAnimAddress(address, { expectedHrp: 'anim' })).toThrow('Invalid address prefix');
  });
});
