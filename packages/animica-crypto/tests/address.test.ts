import { describe, it, expect } from 'vitest';
import { addressFromPubkey, decodeAddress, validateAddress, shortAddress, HRP } from '../src/address';
import { AlgorithmId } from '../src/algorithms';
import { hexToBytes } from '../src/utils';

describe('Address', () => {
  describe('addressFromPubkey', () => {
    it('should generate valid bech32m address', () => {
      // Create a dummy 1952-byte public key (Dilithium3)
      const pubkey = new Uint8Array(1952).fill(0x42);
      
      const address = addressFromPubkey(AlgorithmId.DILITHIUM3, pubkey);
      
      // Should start with "anim1"
      expect(address).toMatch(/^anim1/);
      // Should be reasonable length
      expect(address.length).toBeGreaterThan(10);
      expect(address.length).toBeLessThan(90);
    });
    
    it('should be deterministic', () => {
      const pubkey = new Uint8Array(1952).fill(0x42);
      
      const addr1 = addressFromPubkey(AlgorithmId.DILITHIUM3, pubkey);
      const addr2 = addressFromPubkey(AlgorithmId.DILITHIUM3, pubkey);
      
      expect(addr1).toBe(addr2);
    });
    
    it('should support custom HRP', () => {
      const pubkey = new Uint8Array(1952).fill(0x42);
      
      const address = addressFromPubkey(AlgorithmId.DILITHIUM3, pubkey, 'test');
      
      expect(address).toMatch(/^test1/);
    });
  });
  
  describe('decodeAddress', () => {
    it('should decode address back to components', () => {
      const pubkey = new Uint8Array(1952).fill(0x42);
      const address = addressFromPubkey(AlgorithmId.DILITHIUM3, pubkey);
      
      const record = decodeAddress(address);
      
      expect(record.hrp).toBe(HRP);
      expect(record.algId).toBe(AlgorithmId.DILITHIUM3);
      expect(record.digest).toBeInstanceOf(Uint8Array);
      expect(record.digest.length).toBe(32); // SHA3-256 output
    });
    
    it('should verify HRP if specified', () => {
      const pubkey = new Uint8Array(1952).fill(0x42);
      const address = addressFromPubkey(AlgorithmId.DILITHIUM3, pubkey);
      
      expect(() => decodeAddress(address, 'anim')).not.toThrow();
      expect(() => decodeAddress(address, 'wrong')).toThrow(/HRP mismatch/);
    });
  });
  
  describe('validateAddress', () => {
    it('should validate valid address', () => {
      const pubkey = new Uint8Array(1952).fill(0x42);
      const address = addressFromPubkey(AlgorithmId.DILITHIUM3, pubkey);
      
      expect(validateAddress(address)).toBe(true);
    });
    
    it('should reject invalid addresses', () => {
      expect(() => validateAddress('invalid')).toThrow();
      expect(() => validateAddress('anim1invalid')).toThrow();
    });
    
    it('should check allowed algorithm IDs', () => {
      const pubkey = new Uint8Array(1952).fill(0x42);
      const address = addressFromPubkey(AlgorithmId.DILITHIUM3, pubkey);
      
      const allowedDilithium = new Set([AlgorithmId.DILITHIUM3]);
      const allowedSphincs = new Set([AlgorithmId.SPHINCS_SHAKE_128S]);
      
      expect(validateAddress(address, undefined, allowedDilithium)).toBe(true);
      expect(() => validateAddress(address, undefined, allowedSphincs)).toThrow(/not allowed/);
    });
  });
  
  describe('shortAddress', () => {
    it('should shorten long addresses', () => {
      const address = 'anim1qq9a7x3k4l2m5n8p9r2s3t4v5w6x7y8z9a0b1c2d3e4f5g6h7j8k9';
      const short = shortAddress(address, 6);
      
      expect(short).toBe('anim1q…7j8k9');
      expect(short.length).toBeLessThan(address.length);
    });
    
    it('should not shorten short addresses', () => {
      const address = 'anim1qq';
      const short = shortAddress(address, 6);
      
      expect(short).toBe(address);
    });
  });
  
  describe('round-trip encoding', () => {
    it('should encode and decode correctly', () => {
      const pubkey = new Uint8Array(1952).fill(0x42);
      const address = addressFromPubkey(AlgorithmId.DILITHIUM3, pubkey);
      
      const record = decodeAddress(address);
      
      // Re-encode should produce same address
      const address2 = addressFromPubkey(record.algId, pubkey);
      expect(address2).toBe(address);
    });
  });
});
