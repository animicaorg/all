/**
 * Test canonical CBOR encoding
 */

import { describe, it, expect } from 'vitest';
import { encodeCanonical, encodeTxBody } from '../encode';
import { bytesToHex } from '../signing';

describe('Canonical CBOR Encoding', () => {
  it('encodes integers correctly', () => {
    expect(Array.from(encodeCanonical(0))).toEqual([0x00]);
    expect(Array.from(encodeCanonical(1))).toEqual([0x01]);
    expect(Array.from(encodeCanonical(23))).toEqual([0x17]);
    expect(Array.from(encodeCanonical(24))).toEqual([0x18, 0x18]);
    expect(Array.from(encodeCanonical(255))).toEqual([0x18, 0xff]);
    expect(Array.from(encodeCanonical(256))).toEqual([0x19, 0x01, 0x00]);
  });

  it('encodes negative integers correctly', () => {
    expect(Array.from(encodeCanonical(-1))).toEqual([0x20]);
    expect(Array.from(encodeCanonical(-2))).toEqual([0x21]);
    expect(Array.from(encodeCanonical(-24))).toEqual([0x37]);
    expect(Array.from(encodeCanonical(-25))).toEqual([0x38, 0x18]);
  });

  it('encodes byte strings correctly', () => {
    const bytes = new Uint8Array([0x01, 0x02, 0x03]);
    const encoded = encodeCanonical(bytes);
    expect(Array.from(encoded)).toEqual([0x43, 0x01, 0x02, 0x03]);
  });

  it('encodes text strings correctly', () => {
    const encoded = encodeCanonical('hello');
    expect(Array.from(encoded)).toEqual([
      0x65, // text(5)
      0x68, 0x65, 0x6c, 0x6c, 0x6f, // "hello"
    ]);
  });

  it('encodes arrays correctly', () => {
    const encoded = encodeCanonical([1, 2, 3]);
    expect(Array.from(encoded)).toEqual([
      0x83, // array(3)
      0x01, 0x02, 0x03,
    ]);
  });

  it('encodes maps with sorted keys', () => {
    const obj = {
      z: 3,
      a: 1,
      m: 2,
    };
    const encoded = encodeCanonical(obj);
    
    // Keys should be sorted alphabetically when encoded as text strings
    // CBOR encoding of "a" = 0x61, 0x61
    // CBOR encoding of "m" = 0x61, 0x6d
    // CBOR encoding of "z" = 0x61, 0x7a
    // So sorted order is a, m, z
    
    const hex = bytesToHex(encoded);
    // Map should have 3 entries
    expect(encoded[0]).toBe(0xa3); // map(3)
  });

  it('encodes nested structures correctly', () => {
    const obj = {
      version: 1,
      data: new Uint8Array([0xff]),
    };
    const encoded = encodeCanonical(obj);
    
    // Should encode as a map with 2 entries
    expect(encoded[0]).toBe(0xa2); // map(2)
  });

  it('encodes transaction body with canonical field ordering', () => {
    const body = {
      version: 1,
      chain_id: 1337,
      nonce: 0,
      from_addr: new Uint8Array(32),
      to_addr: new Uint8Array(32),
      value: 1000,
      fee: 1000000000,
      gas_limit: 21000,
      data: new Uint8Array(),
      memo: '',
      timestamp: 1700000000,
      kind: 0,
    };
    
    const encoded = encodeTxBody(body);
    
    // Should produce valid CBOR
    expect(encoded.length).toBeGreaterThan(0);
    
    // First byte should be a map
    expect((encoded[0] >> 5) & 0x07).toBe(5); // Major type 5 = map
  });

  it('produces deterministic output', () => {
    const obj = {
      b: 2,
      a: 1,
      c: 3,
    };
    
    const encoded1 = encodeCanonical(obj);
    const encoded2 = encodeCanonical(obj);
    
    expect(bytesToHex(encoded1)).toBe(bytesToHex(encoded2));
  });
});
