/**
 * Integration test for Dilithium3 backend loading
 */

import { describe, it, expect } from 'vitest';
import * as d3 from '../../src/background/pq/dilithium3';

describe('Dilithium3 Backend Integration', () => {
  it('should load backend successfully', async () => {
    const available = await d3.isAvailable();
    expect(available).toBe(true);
  });
  
  it('should generate keypair from seed', async () => {
    const seed = new Uint8Array(32);
    for (let i = 0; i < 32; i++) {
      seed[i] = i;
    }
    
    const { publicKey, secretKey } = await d3.keypairFromSeed(seed);
    
    expect(publicKey.length).toBe(d3.PK_BYTES);
    expect(secretKey.length).toBe(d3.SK_BYTES);
  });
  
  it('should sign and verify message', async () => {
    const seed = new Uint8Array(32);
    for (let i = 0; i < 32; i++) {
      seed[i] = i + 1;
    }
    
    const { publicKey, secretKey } = await d3.keypairFromSeed(seed);
    const message = new TextEncoder().encode('test message');
    
    const signature = await d3.sign(message, secretKey);
    expect(signature.length).toBe(d3.SIG_BYTES);
    
    const isValid = await d3.verify(message, signature, publicKey);
    expect(isValid).toBe(true);
  });
  
  it('should produce deterministic signatures', async () => {
    const seed = new Uint8Array(32);
    for (let i = 0; i < 32; i++) {
      seed[i] = i + 2;
    }
    
    const { secretKey } = await d3.keypairFromSeed(seed);
    const message = new TextEncoder().encode('deterministic test');
    
    const sig1 = await d3.sign(message, secretKey);
    const sig2 = await d3.sign(message, secretKey);
    
    expect(sig1).toEqual(sig2);
  });
});
