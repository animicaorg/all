import { describe, it, expect } from 'vitest';
import { parseWalletsJson, exportWalletsJson, deduplicateAccounts, mergeAccounts } from '../src/core/wallets/import';
import { addressFromPubkey } from '../src/core/crypto/address';
import type { Account } from '../src/types/wallet';
import { NETWORKS } from '../src/types/network';

const SAMPLE_PUBKEY_HEX = '0x1234567890abcdef1234567890abcdef1234567890abcdef1234567890abcdef';
const SAMPLE_SECRET_HEX = '0xfedcba0987654321';
const SAMPLE_ADDRESS_V1 = addressFromPubkey(
  Uint8Array.from(Buffer.from(SAMPLE_PUBKEY_HEX.slice(2), 'hex')),
  4097,
  { expectedHrp: 'anim', supportedVersions: [1, 2] },
);
const SAMPLE_ADDRESS_V2 = addressFromPubkey(
  Uint8Array.from(Buffer.from(SAMPLE_PUBKEY_HEX.slice(2), 'hex')),
  4097,
  { expectedHrp: 'anim', supportedVersions: [2, 1] },
);

describe('wallets.json Import/Export', () => {
  const sampleWalletsJson = `{
    "format": "animica.wallets",
    "version": 2,
    "created_at": "2025-01-01T00:00:00Z",
    "updated_at": "2025-01-01T00:00:00Z",
    "wallets": [
      {
        "label": "test-account",
        "address": "${SAMPLE_ADDRESS_V1}",
        "alg_id": 4097,
        "alg_name": "dilithium3",
        "public_key_hex": "${SAMPLE_PUBKEY_HEX}",
        "secret_key_hex": "${SAMPLE_SECRET_HEX}",
        "created_at": "2025-01-01T00:00:00Z"
      }
    ]
  }`;

  it('parses canonical and legacy wallets.json', () => {
    expect(() => parseWalletsJson(sampleWalletsJson)).not.toThrow();
    const legacy = JSON.stringify({ wallets: [{
      label: 'legacy',
      address: SAMPLE_ADDRESS_V1,
      algId: 4097,
      publicKeyHex: SAMPLE_PUBKEY_HEX,
      createdAt: '2025-01-01T00:00:00Z',
    }]});
    expect(() => parseWalletsJson(legacy)).not.toThrow();
  });

  it('accepts v2 addresses when network supports v2', () => {
    const json = JSON.stringify({
      format: 'animica.wallets',
      version: 2,
      created_at: '2025-01-01T00:00:00Z',
      updated_at: '2025-01-01T00:00:00Z',
      wallets: [{
        label: 'v2-wallet',
        address: SAMPLE_ADDRESS_V2,
        alg_id: 4097,
        public_key_hex: SAMPLE_PUBKEY_HEX,
        created_at: '2025-01-01T00:00:00Z',
      }],
    });

    const accounts = parseWalletsJson(json, { network: NETWORKS.mainnet });
    expect(accounts).toHaveLength(1);
  });

  it('shows switch-network guidance for unsupported version on selected network', () => {
    const json = JSON.stringify({
      format: 'animica.wallets',
      version: 2,
      created_at: '2025-01-01T00:00:00Z',
      updated_at: '2025-01-01T00:00:00Z',
      wallets: [{
        label: 'v2-wallet',
        address: SAMPLE_ADDRESS_V2,
        alg_id: 4097,
        public_key_hex: SAMPLE_PUBKEY_HEX,
        created_at: '2025-01-01T00:00:00Z',
      }],
    });

    const sameHrpV1OnlyNetwork = { ...NETWORKS.mainnet, supportedAddressVersions: [1] };
    expect(() => parseWalletsJson(json, { network: sameHrpV1OnlyNetwork }))
      .toThrow('Switch network and retry import.');
  });

  it('validates chain_id metadata against selected network', () => {
    const json = JSON.stringify({
      wallets: [{
        label: 'wrong-chain',
        address: SAMPLE_ADDRESS_V1,
        alg_id: 4097,
        public_key_hex: SAMPLE_PUBKEY_HEX,
        created_at: '2025-01-01T00:00:00Z',
        meta: { chain_id: 999 },
      }],
    });

    expect(() => parseWalletsJson(json, { network: NETWORKS.mainnet }))
      .toThrow('targets chain_id 999, but current network is 1. Switch network and retry import.');
  });

  it('exports canonical version 2 wallets.json', () => {
    const accounts: Account[] = [{
      label: 'test',
      address: SAMPLE_ADDRESS_V1,
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
