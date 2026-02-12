import { describe, it, expect } from 'vitest';
import { parseWalletsJson, exportWalletsJson, deduplicateAccounts, mergeAccounts } from '../src/core/wallets/import';
import type { Account } from '../src/types/wallet';

describe('wallets.json Import/Export', () => {
  const sampleWalletsJson = `{
    "format": "animica.wallets",
    "version": 2,
    "created_at": "2025-01-01T00:00:00Z",
    "updated_at": "2025-01-01T00:00:00Z",
    "wallets": [
      {
        "label": "test-account",
        "address": "anim1qqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqz6j8g5",
        "alg_id": 4097,
        "alg_name": "dilithium3",
        "public_key_hex": "0x1234567890abcdef",
        "secret_key_hex": "0xfedcba0987654321",
        "created_at": "2025-01-01T00:00:00Z"
      }
    ]
  }`;

  it('parses canonical and legacy wallets.json', () => {
    expect(() => parseWalletsJson(sampleWalletsJson)).not.toThrow();
    const legacy = JSON.stringify({ wallets: [{
      label: 'legacy',
      address: 'anim1qqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqz6j8g5',
      algId: 4097,
      publicKeyHex: '0x1234567890abcdef',
      createdAt: '2025-01-01T00:00:00Z',
    }]});
    expect(() => parseWalletsJson(legacy)).not.toThrow();
  });

  it('exports canonical version 2 wallets.json', () => {
    const accounts: Account[] = [{
      label: 'test',
      address: 'anim1qqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqz6j8g5',
      algId: 4097,
      algName: 'dilithium3',
      publicKey: new Uint8Array([1, 2, 3, 4]),
      secretKey: new Uint8Array([4, 5, 6]),
      createdAt: '2025-01-01T00:00:00Z',
    }];

    const exported = exportWalletsJson(accounts, true);
    const parsed = JSON.parse(exported);

    expect(parsed.format).toBe('animica.wallets');
    expect(parsed.version).toBe(2);
    expect(parsed.wallets[0].secret_key_hex).toBe('0x040506');
  });

  it('deduplicates accounts by address', () => {
    const accounts: Account[] = [
      { label: 'A', address: 'anim1abc', algId: 4097, algName: 'dilithium3', publicKey: new Uint8Array([1]), createdAt: '2025-01-01T00:00:00Z' },
      { label: 'B', address: 'anim1abc', algId: 4097, algName: 'dilithium3', publicKey: new Uint8Array([1]), createdAt: '2025-01-02T00:00:00Z' },
      { label: 'C', address: 'anim1def', algId: 4097, algName: 'dilithium3', publicKey: new Uint8Array([2]), createdAt: '2025-01-03T00:00:00Z' },
    ];

    const deduped = deduplicateAccounts(accounts);
    expect(deduped).toHaveLength(2);
  });

  it('merges accounts preferring imported secrets', () => {
    const existing: Account[] = [{
      label: 'Watch', address: 'anim1abc', algId: 4097, algName: 'dilithium3', publicKey: new Uint8Array([1]), watchOnly: true, createdAt: '2025-01-01T00:00:00Z',
    }];
    const imported: Account[] = [{
      label: 'Full', address: 'anim1abc', algId: 4097, algName: 'dilithium3', publicKey: new Uint8Array([1]), secretKey: new Uint8Array([9]), createdAt: '2025-01-02T00:00:00Z',
    }];

    const merged = mergeAccounts(existing, imported);
    expect(merged).toHaveLength(1);
    expect(merged[0].secretKey).toBeDefined();
  });
});
