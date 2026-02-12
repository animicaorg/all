import { describe, expect, it, vi } from 'vitest';
import { addressFromPubkey } from '../../core/crypto/address';
import { bytesToHex } from '../../core/crypto/pq';
import { importWalletRecords } from './importer';
import * as animicaAddressModule from '../address/animicaAddress';

function testWallet(label: string, seed: number, withSecret = true) {
  const publicKey = Uint8Array.from(Array.from({ length: 32 }, (_, idx) => (seed + idx) % 256));
  const secretKey = Uint8Array.from(Array.from({ length: 32 }, (_, idx) => (seed + idx + 64) % 256));
  const address = addressFromPubkey(publicKey, 0x1001, { expectedHrp: 'anim' });

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
  it('parses versioned wallets file and imports addresses', async () => {
    const payload = {
      version: 1,
      wallets: [
        testWallet('wallet-1', 10),
        testWallet('wallet-2', 20),
      ],
    };

    const result = await importWalletRecords(JSON.stringify(payload), []);
    expect(result.summary.imported_count).toBe(2);
    expect(result.summary.invalid_records).toHaveLength(0);
    expect(result.accounts[1].watchOnly).toBe(false);
  });

  it('deduplicates and upgrades watch-only account', async () => {
    const existingWatchOnly = testWallet('watch', 30, false);
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
        testWallet('watch', 30, true),
        testWallet('watch-duplicate', 30, true),
      ],
    };

    const result = await importWalletRecords(JSON.stringify(payload), existing as any);
    expect(result.summary.upgraded_watch_only).toBe(1);
    expect(result.summary.skipped_duplicates).toBe(1);
    expect(result.accounts[0].watchOnly).toBe(false);
    expect(result.accounts[0].secretKey).toBeDefined();
  });


  it('imports wallets with excess bech32m padding by canonicalizing address from pubkey', async () => {
    const decodeSpy = vi.spyOn(animicaAddressModule, 'decodeAnimAddress');
    decodeSpy.mockImplementationOnce(() => {
      throw new Error('Excess padding');
    });

    const payload = {
      version: 1,
      wallets: [testWallet('bad-padding', 60, true)],
    };

    try {
      const result = await importWalletRecords(JSON.stringify(payload), []);
      expect(result.summary.imported_count).toBe(1);
      expect(result.summary.invalid_records).toHaveLength(0);
    } finally {
      decodeSpy.mockRestore();
    }
  });

  it('still reports non-padding address errors as invalid records', async () => {
    const decodeSpy = vi.spyOn(animicaAddressModule, 'decodeAnimAddress').mockImplementationOnce(() => {
      throw new Error('Invalid checksum');
    });

    const payload = {
      version: 1,
      wallets: [testWallet('bad-checksum', 70, true)],
    };

    try {
      const result = await importWalletRecords(JSON.stringify(payload), []);
      expect(result.summary.imported_count).toBe(0);
      expect(result.summary.invalid_records).toHaveLength(1);
      expect(result.summary.invalid_records[0].reason).toContain('Invalid checksum');
    } finally {
      decodeSpy.mockRestore();
    }
  });
  it('supports single-wallet object and array payloads', async () => {
    const single = testWallet('single', 40, false);
    const fromSingle = await importWalletRecords(JSON.stringify(single), []);
    expect(fromSingle.summary.imported_count).toBe(1);

    const fromArray = await importWalletRecords(JSON.stringify([testWallet('array', 50, false)]), []);
    expect(fromArray.summary.imported_count).toBe(1);
  });
});
