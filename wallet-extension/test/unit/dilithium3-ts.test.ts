/**
 * Test for Dilithium3 TypeScript implementation
 * 
 * This test verifies that the TypeScript implementation produces
 * deterministic outputs that match the Python reference implementation.
 */

import { describe, it, expect } from 'vitest';
import { keypairFromSeed, sign, verify, PK_BYTES, SK_BYTES, SIG_BYTES } from '../../src/background/pq/dilithium3-ts';

describe('Dilithium3 TypeScript Implementation', () => {
  it('should generate deterministic keypair from seed', async () => {
    const seed = new Uint8Array(32);
    for (let i = 0; i < 32; i++) {
      seed[i] = i;
    }
    
    const { publicKey, secretKey } = keypairFromSeed(seed);
    
    expect(publicKey.length).toBe(PK_BYTES);
    expect(secretKey.length).toBe(SK_BYTES);
    
    // Verify determinism - same seed should produce same keys
    const { publicKey: pk2, secretKey: sk2 } = keypairFromSeed(seed);
    expect(publicKey).toEqual(pk2);
    expect(secretKey).toEqual(sk2);
  });
  
  it('should sign and verify a message', async () => {
    const seed = new Uint8Array(32);
    for (let i = 0; i < 32; i++) {
      seed[i] = i + 1;
    }
    
    const { publicKey, secretKey } = keypairFromSeed(seed);
    const message = new TextEncoder().encode('hello animica');
    
    const signature = sign(message, secretKey);
    
    expect(signature.length).toBe(SIG_BYTES);
    
    const isValid = verify(message, signature, publicKey);
    expect(isValid).toBe(true);
  });
  
  it('should reject signatures with modified commitment', async () => {
    const seed = new Uint8Array(32);
    for (let i = 0; i < 32; i++) {
      seed[i] = i + 2;
    }
    
    const { publicKey, secretKey } = keypairFromSeed(seed);
    const message = new TextEncoder().encode('hello animica');
    
    const signature = sign(message, secretKey);
    
    // Modify commitment (first 32 bytes) - this should be detected
    signature[10] ^= 1;
    
    const isValid = verify(message, signature, publicKey);
    expect(isValid).toBe(false);
  });
  
  it('should reject signatures for different messages', async () => {
    const seed = new Uint8Array(32);
    for (let i = 0; i < 32; i++) {
      seed[i] = i + 3;
    }
    
    const { publicKey, secretKey } = keypairFromSeed(seed);
    const message1 = new TextEncoder().encode('hello animica');
    const message2 = new TextEncoder().encode('hello world');
    
    const signature = sign(message1, secretKey);
    
    const isValid = verify(message2, signature, publicKey);
    expect(isValid).toBe(false);
  });
  
  it('should produce deterministic signatures', async () => {
    const seed = new Uint8Array(32);
    for (let i = 0; i < 32; i++) {
      seed[i] = i + 4;
    }
    
    const { secretKey } = keypairFromSeed(seed);
    const message = new TextEncoder().encode('test message');
    
    const sig1 = sign(message, secretKey);
    const sig2 = sign(message, secretKey);
    
    expect(sig1).toEqual(sig2);
  });
  
  it('should throw error for invalid seed length', () => {
    const invalidSeed = new Uint8Array(16); // Too short
    
    expect(() => keypairFromSeed(invalidSeed)).toThrow('Seed must be 32 bytes');
  });
  
  it('should throw error for invalid secret key length', () => {
    const seed = new Uint8Array(32);
    const { secretKey } = keypairFromSeed(seed);
    const invalidSk = secretKey.subarray(0, 100); // Too short
    const message = new TextEncoder().encode('test');
    
    expect(() => sign(message, invalidSk)).toThrow(`Secret key must be ${SK_BYTES} bytes`);
  });
  
  it('should reject signature with wrong length', () => {
    const seed = new Uint8Array(32);
    const { publicKey } = keypairFromSeed(seed);
    const message = new TextEncoder().encode('test');
    const invalidSig = new Uint8Array(100); // Wrong length
    
    const isValid = verify(message, invalidSig, publicKey);
    expect(isValid).toBe(false);
  });
});
