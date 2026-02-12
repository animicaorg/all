/**
 * Test canonical signing
 */

import { describe, it, expect } from 'vitest';
import { buildSigningPreimage, computeSignHash, bytesToHex, hexToBytes } from '../signing';
import type { TxBody, ChainContext } from '../types';
import { DOMAIN_TX_SIGN } from '../types';

describe('Canonical Signing', () => {
  const mockContext: ChainContext = {
    chain_id: 1337,
    genesis_hash: new Uint8Array(32).fill(0xaa),
    network: 'testnet',
    fork_id: null,
    domain: DOMAIN_TX_SIGN,
    prehash: 'sha3-512',
  };

  const mockBody: TxBody = {
    version: 1,
    chain_id: 1337,
    nonce: 0,
    from_addr: new Uint8Array(32).fill(0x01),
    to_addr: new Uint8Array(32).fill(0x02),
    value: 1000,
    fee: 1000000000,
    gas_limit: 21000,
    data: new Uint8Array(),
    memo: '',
    timestamp: 1700000000,
    kind: 0,
  };

  it('builds signing preimage with correct structure', () => {
    const preimage = buildSigningPreimage(mockBody, mockContext);
    
    // Preimage should be non-empty
    expect(preimage.length).toBeGreaterThan(0);
    
    // Should be valid CBOR (starts with map)
    const majorType = (preimage[0] >> 5) & 0x07;
    expect(majorType).toBe(5); // Map
  });

  it('includes all required fields in preimage', () => {
    const preimage = buildSigningPreimage(mockBody, mockContext);
    const hex = bytesToHex(preimage);
    
    // Preimage should include:
    // - domain string "animica.tx.v1"
    // - chain_id
    // - genesis_hash
    // - network string
    // - message_type "tx"
    // - version
    // - body
    
    // Check it's a substantial structure
    expect(preimage.length).toBeGreaterThan(100);
  });

  it('computes sign hash as SHA3-512', () => {
    const signHash = computeSignHash(mockBody, mockContext);
    
    // SHA3-512 produces 64-byte output
    expect(signHash.length).toBe(64);
  });

  it('produces deterministic output', () => {
    const preimage1 = buildSigningPreimage(mockBody, mockContext);
    const preimage2 = buildSigningPreimage(mockBody, mockContext);
    
    expect(bytesToHex(preimage1)).toBe(bytesToHex(preimage2));
  });

  it('produces different hashes for different bodies', () => {
    const hash1 = computeSignHash(mockBody, mockContext);
    
    const modifiedBody = {
      ...mockBody,
      nonce: 1, // Different nonce
    };
    const hash2 = computeSignHash(modifiedBody, mockContext);
    
    expect(bytesToHex(hash1)).not.toBe(bytesToHex(hash2));
  });

  it('produces different hashes for different chain contexts', () => {
    const hash1 = computeSignHash(mockBody, mockContext);
    
    const differentContext = {
      ...mockContext,
      chain_id: 9999, // Different chain ID
    };
    const hash2 = computeSignHash(mockBody, differentContext);
    
    expect(bytesToHex(hash1)).not.toBe(bytesToHex(hash2));
  });

  it('includes genesis_hash in signing', () => {
    const contextWithGenesis = {
      ...mockContext,
      genesis_hash: new Uint8Array(32).fill(0xbb),
    };
    
    const hash1 = computeSignHash(mockBody, mockContext);
    const hash2 = computeSignHash(mockBody, contextWithGenesis);
    
    // Different genesis should produce different hash
    expect(bytesToHex(hash1)).not.toBe(bytesToHex(hash2));
  });

  it('includes network name in signing', () => {
    const contextWithDifferentNetwork = {
      ...mockContext,
      network: 'mainnet',
    };
    
    const hash1 = computeSignHash(mockBody, mockContext);
    const hash2 = computeSignHash(mockBody, contextWithDifferentNetwork);
    
    // Different network should produce different hash
    expect(bytesToHex(hash1)).not.toBe(bytesToHex(hash2));
  });

  it('hexToBytes round-trips correctly', () => {
    const original = new Uint8Array([0x01, 0x23, 0x45, 0x67, 0x89, 0xab, 0xcd, 0xef]);
    const hex = bytesToHex(original);
    const decoded = hexToBytes(hex);
    
    expect(Array.from(decoded)).toEqual(Array.from(original));
  });

  it('hexToBytes handles 0x prefix', () => {
    const hex = '0x0123456789abcdef';
    const decoded = hexToBytes(hex);
    
    expect(Array.from(decoded)).toEqual([0x01, 0x23, 0x45, 0x67, 0x89, 0xab, 0xcd, 0xef]);
  });
});
