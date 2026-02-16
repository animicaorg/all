import { describe, expect, it } from 'vitest';
import {
  chooseBestWalletScheme,
  resolveSchemeIdForWalletAlgo,
  resolveWalletSchemeSelection,
} from '../../src/background/network/signatureSchemes';

describe('signature scheme negotiation', () => {
  const schemes = [
    {
      schemeId: 1,
      name: 'dilithium3',
      pubkeyLengths: [1952],
      signatureLengths: [3293],
      enabledByCode: true,
      enabledByPolicy: true,
      enabledEffective: true,
    },
    {
      schemeId: 2,
      name: 'sphincs_shake_128s',
      pubkeyLengths: [32],
      signatureLengths: [7856],
      enabledByCode: true,
      enabledByPolicy: true,
      enabledEffective: true,
    },
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

  it('returns a user-facing switch message when selected scheme is disabled', () => {
    const filtered = [
      { ...schemes[0], enabledByPolicy: true, enabledEffective: true },
      { ...schemes[1], enabledByPolicy: false, enabledEffective: false },
    ];
    const selected = resolveWalletSchemeSelection(filtered, 'sphincs_shake_128s');
    expect(selected.selected).toBe('dilithium3');
    expect(selected.message).toContain('This network currently disallows sphincs_shake_128s. Switched to dilithium3.');
  });
});
