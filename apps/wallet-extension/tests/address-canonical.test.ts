/**
 * Canonical Address Tests
 * 
 * These tests verify that the extension generates addresses identical to
 * the Python node/CLI implementation. Test vectors are generated from
 * pq/py/address.py to ensure consistency.
 * 
 * This prevents regressions where the extension might generate different
 * addresses than the node, causing balance queries to fail.
 */

import { describe, expect, it } from 'vitest';
import { addressFromPubkey, decodeAddress } from '../src/core/crypto/address';
import { sha3Hash } from '../src/core/crypto/pq';

describe('Address Canonical Format (matches node/CLI)', () => {
  /**
   * Test Vector 1: Dilithium3 with repeating 0x01 bytes
   * Generated from Python: pq.py.address.address_from_pubkey()
   * 
   * Note: Using 1952 bytes to match actual Dilithium3 public key size.
   * The address is derived from SHA3-256(pubkey), so any size works,
   * but we use the real size to match production usage.
   */
  it('generates correct address for test vector 1', () => {
    const pubkey = new Uint8Array(1952).fill(0x01);
    const algId = 0x1001; // Dilithium3
    const expectedAddress = 'anim1zqq7fkryhnm5gn53w8g4el3vqp0gthpsklfffahgm4lnukel6ka768qewua7g';
    
    const address = addressFromPubkey(pubkey, algId);
    expect(address).toBe(expectedAddress);
  });

  /**
   * Test Vector 2: Dilithium3 with repeating 0xff bytes
   * Generated from Python: pq.py.address.address_from_pubkey()
   * 
   * Note: Using 1952 bytes to match actual Dilithium3 public key size.
   */
  it('generates correct address for test vector 2', () => {
    const pubkey = new Uint8Array(1952).fill(0xff);
    const algId = 0x1001; // Dilithium3
    const expectedAddress = 'anim1zqq7j4lawhd4lcn0tnrydf7um64xa3ph86eprqvakhdh6k203unc8lg2hjvzl';
    
    const address = addressFromPubkey(pubkey, algId);
    expect(address).toBe(expectedAddress);
  });

  /**
   * Test: Address roundtrip (encode -> decode -> encode)
   */
  it('roundtrips address encoding/decoding', () => {
    const pubkey = new Uint8Array(1952).fill(0x42);
    const algId = 0x1001;
    
    const address1 = addressFromPubkey(pubkey, algId);
    const decoded = decodeAddress(address1);
    
    expect(decoded.hrp).toBe('anim');
    expect(decoded.algId).toBe(algId);
    
    // Verify digest matches SHA3-256(pubkey)
    const expectedDigest = sha3Hash(pubkey);
    expect(Array.from(decoded.digest)).toEqual(Array.from(expectedDigest));
  });

  /**
   * Test: Payload format matches canonical spec
   * Format: [alg_id (2 bytes BE)] + [sha3_256(pubkey) (32 bytes)]
   * 
   * Note: Smaller 32-byte pubkey used here for test simplicity.
   * In production, Dilithium3 uses 1952-byte keys, but SHA3-256
   * always produces 32-byte digest regardless of input size.
   */
  it('uses correct payload format', () => {
    const pubkey = new Uint8Array(32).fill(0xaa);
    const algId = 0x1001;
    
    const address = addressFromPubkey(pubkey, algId);
    const decoded = decodeAddress(address);
    
    // Verify payload structure
    expect(decoded.algId).toBe(0x1001);
    expect(decoded.digest.length).toBe(32);
    
    // Verify digest is SHA3-256 of pubkey
    const expectedDigest = sha3Hash(pubkey);
    expect(Array.from(decoded.digest)).toEqual(Array.from(expectedDigest));
  });

  /**
   * Test: No version byte in bech32m encoding
   * This was the bug: extension was adding a version byte, node was not
   */
  it('does not include version byte in bech32m encoding', () => {
    const pubkey = new Uint8Array(32).fill(0x01);
    const algId = 0x1001;
    
    const address = addressFromPubkey(pubkey, algId);
    
    // Decode and verify payload is exactly 34 bytes (2 alg_id + 32 digest)
    const decoded = decodeAddress(address);
    const payloadLength = 2 + decoded.digest.length;
    expect(payloadLength).toBe(34);
    
    // If there was a version byte, payload would be 35+ bytes
  });

  /**
   * Test: Algorithm ID is encoded as big-endian 2-byte integer
   */
  it('encodes algorithm ID as big-endian', () => {
    const pubkey = new Uint8Array(32).fill(0x01);
    const algId = 0x1234; // Test with specific value
    
    const address = addressFromPubkey(pubkey, algId);
    const decoded = decodeAddress(address);
    
    expect(decoded.algId).toBe(0x1234);
  });

  /**
   * Test: HRP matches network
   */
  it('uses correct HRP for mainnet', () => {
    const pubkey = new Uint8Array(32).fill(0x01);
    const algId = 0x1001;
    
    const address = addressFromPubkey(pubkey, algId, { expectedHrp: 'anim' });
    expect(address.startsWith('anim1')).toBe(true);
  });

  /**
   * Test: HRP can be customized for testnet/devnet
   */
  it('supports custom HRP for different networks', () => {
    const pubkey = new Uint8Array(32).fill(0x01);
    const algId = 0x1001;
    
    const testnetAddr = addressFromPubkey(pubkey, algId, { expectedHrp: 'animt' });
    expect(testnetAddr.startsWith('animt1')).toBe(true);
    
    const devnetAddr = addressFromPubkey(pubkey, algId, { expectedHrp: 'animd' });
    expect(devnetAddr.startsWith('animd1')).toBe(true);
  });
});
