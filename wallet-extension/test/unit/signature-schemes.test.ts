import { describe, expect, it } from 'vitest';
import { chooseBestWalletScheme, resolveSchemeIdForWalletAlgo } from '../../src/background/network/signatureSchemes';

describe('signature scheme negotiation', () => {
  const schemes = [
    { schemeId: 1, name: 'dilithium3', pubkeyLengths: [1952], signatureLengths: [3293], enabled: true },
    { schemeId: 2, name: 'sphincs_shake_128s', pubkeyLengths: [32], signatureLengths: [7856], enabled: true },
  ];

  it('chooses preferred scheme when supported', () => {
    expect(chooseBestWalletScheme(schemes, 'dilithium3')).toBe('dilithium3');
  });

  it('chooses strongest PQ fallback when preferred is unsupported', () => {
    expect(chooseBestWalletScheme(schemes, undefined)).toBe('sphincs_shake_128s');
  });

  it('resolves scheme id from node registry', () => {
    expect(resolveSchemeIdForWalletAlgo('sphincs_shake_128s', schemes)).toBe(2);
  });
});
