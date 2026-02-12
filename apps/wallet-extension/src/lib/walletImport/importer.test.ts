import { describe, expect, it } from 'vitest';
import { addressFromPubkey } from '../../core/crypto/address';
import { bytesToHex } from '../../core/crypto/pq';
import { importWalletRecords } from './importer';

function testWallet(label: string, seed: number, version: 1 | 2, withSecret = true) {
  const publicKey = Uint8Array.from(Array.from({ length: 32 }, (_, idx) => (seed + idx) % 256));
  const secretKey = Uint8Array.from(Array.from({ length: 32 }, (_, idx) => (seed + idx + 64) % 256));
  const address = addressFromPubkey(publicKey, 0x1001, { expectedHrp: 'anim', supportedVersions: [version] });

  return {
    label,
    address,
    alg_id: 0x1001,
    alg_name: 'dilithium3',
    public_key_hex: bytesToHex(publicKey),
    ...(withSecret ? { secret_key_hex: bytesToHex(secretKey) } : {}),
    created_at: '2025-01-01T00:00:00.000Z',
  };
}

describe('wallet importer', () => {
  it('parses versioned wallets file and imports v1/v2 addresses', async () => {
    const payload = {
      version: 1,
      wallets: [
        testWallet('wallet-v1', 10, 1),
        testWallet('wallet-v2', 20, 2),
      ],
    };

    const result = await importWalletRecords(JSON.stringify(payload), []);
    expect(result.summary.imported_count).toBe(2);
    expect(result.summary.invalid_records).toHaveLength(0);
    expect(result.accounts[1].watchOnly).toBe(false);
  });

  it('deduplicates and upgrades watch-only account', async () => {
    const existingWatchOnly = testWallet('watch', 30, 1, false);
    const existing = [
      {
        label: existingWatchOnly.label,
        address: existingWatchOnly.address,
        algId: existingWatchOnly.alg_id,
        algName: existingWatchOnly.alg_name,
        publicKey: Uint8Array.from([]),
        createdAt: existingWatchOnly.created_at,
        watchOnly: true,
      },
    ];

    const payload = {
      version: 1,
      wallets: [
        testWallet('watch', 30, 1, true),
        testWallet('watch-duplicate', 30, 1, true),
      ],
    };

    const result = await importWalletRecords(JSON.stringify(payload), existing as any);
    expect(result.summary.upgraded_watch_only).toBe(1);
    expect(result.summary.skipped_duplicates).toBe(1);
    expect(result.accounts[0].watchOnly).toBe(false);
    expect(result.accounts[0].secretKey).toBeDefined();
  });

  it('supports single-wallet object and array payloads', async () => {
    const single = testWallet('single', 40, 1, false);
    const fromSingle = await importWalletRecords(JSON.stringify(single), []);
    expect(fromSingle.summary.imported_count).toBe(1);

    const fromArray = await importWalletRecords(JSON.stringify([testWallet('array', 50, 2, false)]), []);
    expect(fromArray.summary.imported_count).toBe(1);
  });
});
