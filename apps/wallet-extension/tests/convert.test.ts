import { describe, it, expect } from 'vitest';
import {
  strip0x,
  hexToBytes,
  bytesToHex,
  bytesToHexRaw,
  assertString,
  assertHex,
  assertBytes,
  requireField,
} from '../src/core/crypto/convert';

describe('convert utilities', () => {
  describe('strip0x', () => {
    it('should strip 0x prefix', () => {
      expect(strip0x('0xabcd', 'test')).toBe('abcd');
    });

    it('should return as-is if no prefix', () => {
      expect(strip0x('abcd', 'test')).toBe('abcd');
    });

    it('should throw on undefined', () => {
      expect(() => strip0x(undefined, 'test')).toThrow('Expected test to be a string, got undefined');
    });

    it('should throw on null', () => {
      expect(() => strip0x(null, 'test')).toThrow('Expected test to be a string, got null');
    });

    it('should throw on non-string', () => {
      expect(() => strip0x(123 as any, 'test')).toThrow('Expected test to be a string, got number');
    });
  });

  describe('hexToBytes', () => {
    it('should convert valid hex to bytes', () => {
      const bytes = hexToBytes('0x0102', 'test');
      expect(bytes).toEqual(new Uint8Array([1, 2]));
    });

    it('should handle hex without 0x prefix', () => {
      const bytes = hexToBytes('abcd', 'test');
      expect(bytes).toEqual(new Uint8Array([0xab, 0xcd]));
    });

    it('should throw on undefined', () => {
      expect(() => hexToBytes(undefined, 'test')).toThrow('Expected test to be a string, got undefined');
    });

    it('should throw on null', () => {
      expect(() => hexToBytes(null, 'test')).toThrow('Expected test to be a string, got null');
    });

    it('should throw on empty hex', () => {
      expect(() => hexToBytes('0x', 'test')).toThrow('test is empty');
    });

    it('should throw on odd-length hex', () => {
      expect(() => hexToBytes('0x123', 'test')).toThrow('test has odd length: 3');
    });

    it('should throw on invalid hex characters', () => {
      expect(() => hexToBytes('0xg1', 'test')).toThrow('test contains invalid hex characters');
    });
  });

  describe('bytesToHex', () => {
    it('should convert bytes to hex with 0x prefix', () => {
      const hex = bytesToHex(new Uint8Array([1, 2, 255]), 'test');
      expect(hex).toBe('0x0102ff');
    });

    it('should throw on undefined', () => {
      expect(() => bytesToHex(undefined, 'test')).toThrow('Expected test to be Uint8Array, got undefined');
    });

    it('should throw on null', () => {
      expect(() => bytesToHex(null, 'test')).toThrow('Expected test to be Uint8Array, got null');
    });

    it('should throw on non-Uint8Array', () => {
      expect(() => bytesToHex([1, 2] as any, 'test')).toThrow('Expected test to be Uint8Array, got object');
    });
  });

  describe('bytesToHexRaw', () => {
    it('should convert bytes to hex without 0x prefix', () => {
      const hex = bytesToHexRaw(new Uint8Array([1, 2, 255]), 'test');
      expect(hex).toBe('0102ff');
    });

    it('should throw on undefined', () => {
      expect(() => bytesToHexRaw(undefined, 'test')).toThrow('Expected test to be Uint8Array, got undefined');
    });
  });

  describe('assertString', () => {
    it('should pass for valid non-empty string', () => {
      expect(() => assertString('hello', 'test')).not.toThrow();
    });

    it('should throw for empty string', () => {
      expect(() => assertString('', 'test')).toThrow('test is empty');
    });

    it('should throw for non-string', () => {
      expect(() => assertString(123, 'test')).toThrow('Expected test to be a string, got number');
    });
  });

  describe('assertHex', () => {
    it('should pass for valid hex', () => {
      expect(() => assertHex('0xabcd', 'test')).not.toThrow();
      expect(() => assertHex('abcd', 'test')).not.toThrow();
    });

    it('should throw for odd-length hex', () => {
      expect(() => assertHex('0x123', 'test')).toThrow('test has odd length: 3');
    });

    it('should throw for invalid characters', () => {
      expect(() => assertHex('0xgg', 'test')).toThrow('test contains invalid hex characters');
    });

    it('should throw for empty string', () => {
      expect(() => assertHex('', 'test')).toThrow('test is empty');
    });
  });

  describe('assertBytes', () => {
    it('should pass for Uint8Array', () => {
      expect(() => assertBytes(new Uint8Array([1, 2]), 'test')).not.toThrow();
    });

    it('should throw for non-Uint8Array', () => {
      expect(() => assertBytes([1, 2], 'test')).toThrow('Expected test to be Uint8Array, got object');
    });
  });

  describe('requireField', () => {
    it('should pass when field exists', () => {
      const obj = { name: 'test', value: 123 };
      expect(() => requireField(obj, 'name', 'obj')).not.toThrow();
    });

    it('should throw when field is undefined', () => {
      const obj: { name?: string } = {};
      expect(() => requireField(obj, 'name', 'obj')).toThrow('obj.name is required but undefined');
    });

    it('should pass when field is null (only checks undefined)', () => {
      const obj = { name: null };
      expect(() => requireField(obj, 'name', 'obj')).not.toThrow();
    });
  });

  describe('regression: undefined.slice crash', () => {
    it('hexToBytes should not crash on undefined with clear error', () => {
      let error: Error | undefined;
      try {
        hexToBytes(undefined, 'testField');
      } catch (e) {
        error = e as Error;
      }
      
      expect(error).toBeDefined();
      expect(error!.message).toContain('testField');
      expect(error!.message).toContain('undefined');
      expect(error!.message).not.toContain('slice');
    });

    it('bytesToHex should not crash on undefined with clear error', () => {
      let error: Error | undefined;
      try {
        bytesToHex(undefined, 'testField');
      } catch (e) {
        error = e as Error;
      }
      
      expect(error).toBeDefined();
      expect(error!.message).toContain('testField');
      expect(error!.message).toContain('undefined');
      expect(error!.message).not.toContain('slice');
    });

    it('strip0x should not crash on undefined with clear error', () => {
      let error: Error | undefined;
      try {
        strip0x(undefined, 'testField');
      } catch (e) {
        error = e as Error;
      }
      
      expect(error).toBeDefined();
      expect(error!.message).toContain('testField');
      expect(error!.message).toContain('undefined');
      expect(error!.message).not.toContain('slice');
    });
  });
});
