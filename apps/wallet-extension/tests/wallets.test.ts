import { describe, it, expect } from 'vitest';
import { parseWalletsJson, exportWalletsJson, deduplicateAccounts, mergeAccounts } from '../src/core/wallets/import';
import type { Account } from '../src/types/wallet';

describe('wallets.json Import/Export', () => {
  const sampleWalletsJson = `{
    "version": 1,
    "wallets": [
      {
        "label": "test-account",
        "address": "anim1qp5h0000000000000000000000000000000000000000000000000000",
        "alg_id": 4097,
        "alg_name": "dilithium3",
        "public_key_hex": "0x1234567890abcdef",
        "secret_key_hex": "0xfedcba0987654321",
        "created_at": "2025-01-01T00:00:00Z"
      }
    ]
  }`;

  it('should parse valid wallets.json', () => {
    const accounts = parseWalletsJson(sampleWalletsJson);

    expect(accounts).toHaveLength(1);
    expect(accounts[0].label).toBe('test-account');
    expect(accounts[0].algId).toBe(4097);
    expect(accounts[0].algName).toBe('dilithium3');
  });

  it('should export wallets.json without secrets', () => {
    const accounts: Account[] = [
      {
        label: 'test',
        address: 'anim1test',
        algId: 4097,
        algName: 'dilithium3',
        publicKey: new Uint8Array([1, 2, 3]),
        secretKey: new Uint8Array([4, 5, 6]),
        createdAt: '2025-01-01T00:00:00Z',
      },
    ];

    const exported = exportWalletsJson(accounts, false);
    const parsed = JSON.parse(exported);

    expect(parsed.wallets[0].secret_key_hex).toBe('0x');
  });

  it('should export wallets.json with secrets', () => {
    const accounts: Account[] = [
      {
        label: 'test',
        address: 'anim1test',
        algId: 4097,
        algName: 'dilithium3',
        publicKey: new Uint8Array([1, 2, 3]),
        secretKey: new Uint8Array([4, 5, 6]),
        createdAt: '2025-01-01T00:00:00Z',
      },
    ];

    const exported = exportWalletsJson(accounts, true);
    const parsed = JSON.parse(exported);

    expect(parsed.wallets[0].secret_key_hex).toBe('0x040506');
  });

  it('should deduplicate accounts by address', () => {
    const accounts: Account[] = [
      {
        label: 'Account 1',
        address: 'anim1abc',
        algId: 4097,
        algName: 'dilithium3',
        publicKey: new Uint8Array([1]),
        createdAt: '2025-01-01T00:00:00Z',
      },
      {
        label: 'Account 2',
        address: 'anim1abc',
        algId: 4097,
        algName: 'dilithium3',
        publicKey: new Uint8Array([1]),
        createdAt: '2025-01-02T00:00:00Z',
      },
      {
        label: 'Account 3',
        address: 'anim1def',
        algId: 4097,
        algName: 'dilithium3',
        publicKey: new Uint8Array([2]),
        createdAt: '2025-01-03T00:00:00Z',
      },
    ];

    const deduped = deduplicateAccounts(accounts);

    expect(deduped).toHaveLength(2);
    expect(deduped[0].address).toBe('anim1abc');
    expect(deduped[1].address).toBe('anim1def');
  });

  it('should merge accounts preferring imported secrets', () => {
    const existing: Account[] = [
      {
        label: 'Watch Only',
        address: 'anim1abc',
        algId: 4097,
        algName: 'dilithium3',
        publicKey: new Uint8Array([1]),
        watchOnly: true,
        createdAt: '2025-01-01T00:00:00Z',
      },
    ];

    const imported: Account[] = [
      {
        label: 'Full Account',
        address: 'anim1abc',
        algId: 4097,
        algName: 'dilithium3',
        publicKey: new Uint8Array([1]),
        secretKey: new Uint8Array([9, 9, 9]),
        createdAt: '2025-01-02T00:00:00Z',
      },
    ];

    const merged = mergeAccounts(existing, imported);

    expect(merged).toHaveLength(1);
    expect(merged[0].secretKey).toBeDefined();
    expect(merged[0].label).toBe('Full Account');
  });
});
