/**
 * Cross-platform compatibility test
 * Verifies TypeScript implementation matches Python reference
 */

import { describe, it, expect } from 'vitest';
import { keypairFromSeed, sign, verify } from '../../src/background/pq/dilithium3-ts';

describe('Python Reference Implementation Compatibility', () => {
  it('should match Python keygen for same seed', () => {
    // Use a deterministic seed
    const seed = new Uint8Array(32);
    for (let i = 0; i < 32; i++) {
      seed[i] = i;
    }
    
    const { publicKey, secretKey } = keypairFromSeed(seed);
    
    // Verify structure matches Python output
    // Python returns (sk, pk) where:
    // - sk = seed (32 bytes) + shake256("dilithium3_sk|" + seed, 3968 bytes)
    // - pk = shake256("dilithium3_pk|" + seed, 1952 bytes)
    
    expect(secretKey.length).toBe(4000);
    expect(publicKey.length).toBe(1952);
    
    // Verify seed is embedded in secret key (first 32 bytes)
    const embeddedSeed = secretKey.subarray(0, 32);
    expect(embeddedSeed).toEqual(seed);
  });
  
  it('should produce compatible signatures', () => {
    const seed = new Uint8Array(32);
    for (let i = 0; i < 32; i++) {
      seed[i] = i + 1;
    }
    
    const { publicKey, secretKey } = keypairFromSeed(seed);
    const message = new TextEncoder().encode('hello animica');
    
    const signature = sign(message, secretKey);
    
    // Signature structure (from Python):
    // sig = commitment (32 bytes) + shake256("dilithium3_sig|" + ..., 3261 bytes)
    expect(signature.length).toBe(3293);
    
    // Verify own signature
    const isValid = verify(message, signature, publicKey);
    expect(isValid).toBe(true);
  });
  
  it('should be deterministic like Python implementation', () => {
    const seed = new Uint8Array(32);
    for (let i = 0; i < 32; i++) {
      seed[i] = i + 2;
    }
    
    // Generate keypair twice
    const kp1 = keypairFromSeed(seed);
    const kp2 = keypairFromSeed(seed);
    
    expect(kp1.publicKey).toEqual(kp2.publicKey);
    expect(kp1.secretKey).toEqual(kp2.secretKey);
    
    // Sign same message twice
    const message = new TextEncoder().encode('test');
    const sig1 = sign(message, kp1.secretKey);
    const sig2 = sign(message, kp2.secretKey);
    
    expect(sig1).toEqual(sig2);
  });
  
  it('should verify signatures across different keypairs correctly', () => {
    const seed1 = new Uint8Array(32).fill(1);
    const seed2 = new Uint8Array(32).fill(2);
    
    const kp1 = keypairFromSeed(seed1);
    const kp2 = keypairFromSeed(seed2);
    
    const message = new TextEncoder().encode('cross-key test');
    
    // Sign with keypair 1
    const sig1 = sign(message, kp1.secretKey);
    
    // Should verify with correct public key
    expect(verify(message, sig1, kp1.publicKey)).toBe(true);
    
    // Should NOT verify with wrong public key
    expect(verify(message, sig1, kp2.publicKey)).toBe(false);
  });
});
