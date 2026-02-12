import { describe, expect, it } from 'vitest';

import {
  decodeAddress,
  encodeBech32m,
  fromWords,
  toWords,
} from '../../src/utils/bech32';

describe('bech32 legacy padding compatibility', () => {
  it('accepts legacy addresses with excess zero padding when compatibility mode is enabled', () => {
    const payload = new Uint8Array(33);
    payload[0] = 0x01;
    for (let i = 1; i < payload.length; i++) payload[i] = i;

    const words = toWords(payload);
    const legacyWordsWithExcessPadding = [...words, 0];
    const legacyAddress = encodeBech32m('anim', legacyWordsWithExcessPadding);

    expect(() => fromWords(legacyWordsWithExcessPadding)).toThrow('invalid padding');

    const decoded = decodeAddress(legacyAddress, true);
    expect(decoded.hrp).toBe('anim');
    expect(Array.from(decoded.bytes)).toEqual(Array.from(payload));
  });
});
