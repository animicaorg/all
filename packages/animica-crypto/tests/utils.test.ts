import { describe, it, expect } from 'vitest';
import { encodeUvarint, decodeUvarint, lengthPrefix, concatBytes, hexToBytes, bytesToHex, bytesEqual } from '../src/utils';

describe('Utils', () => {
  describe('encodeUvarint', () => {
    it('should encode small numbers', () => {
      expect(encodeUvarint(0)).toEqual(new Uint8Array([0]));
      expect(encodeUvarint(1)).toEqual(new Uint8Array([1]));
      expect(encodeUvarint(127)).toEqual(new Uint8Array([127]));
    });
    
    it('should encode larger numbers', () => {
      expect(encodeUvarint(128)).toEqual(new Uint8Array([0x80, 0x01]));
      expect(encodeUvarint(300)).toEqual(new Uint8Array([0xAC, 0x02]));
      expect(encodeUvarint(1337)).toEqual(new Uint8Array([0xB9, 0x0A]));
    });
    
    it('should reject negative numbers', () => {
      expect(() => encodeUvarint(-1)).toThrow(/non-negative/);
    });
  });
  
  describe('decodeUvarint', () => {
    it('should decode small numbers', () => {
      expect(decodeUvarint(new Uint8Array([0]))).toEqual([0, 1]);
      expect(decodeUvarint(new Uint8Array([1]))).toEqual([1, 1]);
      expect(decodeUvarint(new Uint8Array([127]))).toEqual([127, 1]);
    });
    
    it('should decode larger numbers', () => {
      expect(decodeUvarint(new Uint8Array([0x80, 0x01]))).toEqual([128, 2]);
      expect(decodeUvarint(new Uint8Array([0xAC, 0x02]))).toEqual([300, 2]);
      expect(decodeUvarint(new Uint8Array([0xB9, 0x0A]))).toEqual([1337, 2]);
    });
    
    it('should round-trip encode/decode', () => {
      const testValues = [0, 1, 127, 128, 255, 256, 1337, 65535, 100000];
      for (const val of testValues) {
        const encoded = encodeUvarint(val);
        const [decoded, bytesRead] = decodeUvarint(encoded);
        expect(decoded).toBe(val);
        expect(bytesRead).toBe(encoded.length);
      }
    });
  });
  
  describe('lengthPrefix', () => {
    it('should prefix data with length', () => {
      const data = new Uint8Array([1, 2, 3]);
      const prefixed = lengthPrefix(data);
      
      // First byte should be length (3)
      expect(prefixed[0]).toBe(3);
      // Rest should be data
      expect(prefixed.slice(1)).toEqual(data);
    });
    
    it('should handle empty data', () => {
      const data = new Uint8Array(0);
      const prefixed = lengthPrefix(data);
      
      expect(prefixed.length).toBe(1);
      expect(prefixed[0]).toBe(0);
    });
  });
  
  describe('concatBytes', () => {
    it('should concatenate arrays', () => {
      const a = new Uint8Array([1, 2]);
      const b = new Uint8Array([3, 4]);
      const c = new Uint8Array([5]);
      
      const result = concatBytes(a, b, c);
      
      expect(result).toEqual(new Uint8Array([1, 2, 3, 4, 5]));
    });
    
    it('should handle empty arrays', () => {
      const a = new Uint8Array([1, 2]);
      const b = new Uint8Array(0);
      const c = new Uint8Array([3]);
      
      const result = concatBytes(a, b, c);
      
      expect(result).toEqual(new Uint8Array([1, 2, 3]));
    });
  });
  
  describe('hexToBytes', () => {
    it('should convert hex to bytes', () => {
      expect(hexToBytes('00')).toEqual(new Uint8Array([0]));
      expect(hexToBytes('ff')).toEqual(new Uint8Array([255]));
      expect(hexToBytes('0102')).toEqual(new Uint8Array([1, 2]));
      expect(hexToBytes('abcdef')).toEqual(new Uint8Array([171, 205, 239]));
    });
    
    it('should handle 0x prefix', () => {
      expect(hexToBytes('0x00')).toEqual(new Uint8Array([0]));
      expect(hexToBytes('0xabcd')).toEqual(new Uint8Array([171, 205]));
    });
    
    it('should reject odd-length hex', () => {
      expect(() => hexToBytes('0')).toThrow(/even length/);
      expect(() => hexToBytes('abc')).toThrow(/even length/);
    });
  });
  
  describe('bytesToHex', () => {
    it('should convert bytes to hex', () => {
      expect(bytesToHex(new Uint8Array([0]))).toBe('00');
      expect(bytesToHex(new Uint8Array([255]))).toBe('ff');
      expect(bytesToHex(new Uint8Array([1, 2]))).toBe('0102');
      expect(bytesToHex(new Uint8Array([171, 205, 239]))).toBe('abcdef');
    });
    
    it('should round-trip with hexToBytes', () => {
      const original = new Uint8Array([1, 2, 3, 4, 255, 0, 128]);
      const hex = bytesToHex(original);
      const restored = hexToBytes(hex);
      expect(restored).toEqual(original);
    });
  });
  
  describe('bytesEqual', () => {
    it('should compare equal arrays', () => {
      const a = new Uint8Array([1, 2, 3]);
      const b = new Uint8Array([1, 2, 3]);
      expect(bytesEqual(a, b)).toBe(true);
    });
    
    it('should reject different arrays', () => {
      const a = new Uint8Array([1, 2, 3]);
      const b = new Uint8Array([1, 2, 4]);
      expect(bytesEqual(a, b)).toBe(false);
    });
    
    it('should reject different lengths', () => {
      const a = new Uint8Array([1, 2]);
      const b = new Uint8Array([1, 2, 3]);
      expect(bytesEqual(a, b)).toBe(false);
    });
  });
});
