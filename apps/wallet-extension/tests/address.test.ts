import { describe, expect, it } from 'vitest';
import { bech32m } from 'bech32';
import { addressFromPubkey, decodeAddress, validateAddress } from '../src/core/crypto/address';

const FIXTURE_PUBKEY = new Uint8Array(Array.from({ length: 32 }, (_, i) => i + 1));
const FIXTURE_ALG_ID = 0x1001;

function mutateChecksum(address: string): string {
  const lastChar = address.at(-1);
  const replacement = lastChar === 'q' ? 'p' : 'q';
  return `${address.slice(0, -1)}${replacement}`;
}

describe('address decode and validation', () => {
  const v1Address = addressFromPubkey(FIXTURE_PUBKEY, FIXTURE_ALG_ID, {
    expectedHrp: 'anim',
    supportedVersions: [1, 2],
  });

  const v2Address = addressFromPubkey(FIXTURE_PUBKEY, FIXTURE_ALG_ID, {
    expectedHrp: 'anim',
    supportedVersions: [2, 1],
  });

  it('accepts known-good v1 and v2 addresses', () => {
    expect(decodeAddress(v1Address, { expectedHrp: 'anim', supportedVersions: [1, 2] }).version).toBe(1);
    expect(decodeAddress(v2Address, { expectedHrp: 'anim', supportedVersions: [1, 2] }).version).toBe(2);
  });

  it('rejects unsupported versions with supported list in message', () => {
    expect(() => decodeAddress(v2Address, { expectedHrp: 'anim', supportedVersions: [1] }))
      .toThrow('Unsupported address version 2 (supported: 1)');
  });

  it('rejects wrong HRP', () => {
    expect(() => decodeAddress(v1Address, { expectedHrp: 'animt', supportedVersions: [1, 2] }))
      .toThrow('Invalid address prefix: expected animt, got anim');
  });

  it('rejects wrong checksum', () => {
    const badChecksum = mutateChecksum(v1Address);
    expect(validateAddress(badChecksum, { expectedHrp: 'anim', supportedVersions: [1, 2] })).toBe(false);
  });

  it('rejects wrong payload length', () => {
    const shortPayload = bech32m.encode('anim', [1, ...bech32m.toWords(new Uint8Array(10))]);
    expect(() => decodeAddress(shortPayload, { expectedHrp: 'anim', supportedVersions: [1, 2] }))
      .toThrow('Invalid address payload length: expected 34 bytes, got 10');
  });

  it('rejects unknown algorithm identifier', () => {
    const payload = new Uint8Array(34);
    payload[0] = 0x00;
    payload[1] = 0x01;
    const unknownAlgAddress = bech32m.encode('anim', [1, ...bech32m.toWords(payload)]);
    expect(() => decodeAddress(unknownAlgAddress, { expectedHrp: 'anim', supportedVersions: [1, 2] }))
      .toThrow('Unsupported address algorithm id: 1');
  });
});
