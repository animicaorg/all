/**
 * CBOR utilities (Browser + Node)
 * -----------------------------------------------------------------------------
 * Lightweight wrappers around `cborg` that:
 *  - Accept Uint8Array | 0x-hex | string input for decode.
 *  - Return Uint8Array for encode, with convenient `*Hex` helpers.
 *  - Provide canonical (deterministic) encoding via key sorting.
 *
 * Notes
 * -----
 * - Canonical encoding uses lexicographic UTF-8 key sorting (per RFC 8949).
 * - `cborg` supports BigInt and nested composite types out of the box.
 * - Keep payloads JSON-like for best cross-language compatibility.
 */

import * as cborg from 'cborg';
import { toBytes, bytesToHex, Hex } from './bytes';

export type CborValue =
  | null
  | boolean
  | number
  | bigint
  | string
  | Uint8Array
  | CborValue[]
  | { [k: string]: CborValue };

/* -------------------------------- Encode ----------------------------------- */

/** Encode value to CBOR bytes. */
export function cborEncode(value: CborValue): Uint8Array {
  // Default encoding; stable enough for general use.
  return cborg.encode(value as any);
}

/** Encode value to CBOR bytes using canonical (deterministic) map key ordering. */
export function cborEncodeCanonical(value: CborValue): Uint8Array {
  // RFC 8949 canonical form: sort map keys lexicographically by UTF-8 bytes.
  return cborg.encode(value as any, { sortKeys: true } as any);
}

/** Encode to CBOR and return 0x-hex string. */
export function cborEncodeHex(value: CborValue): Hex {
  return bytesToHex(cborEncode(value));
}

/** Canonical encode to CBOR and return 0x-hex string. */
export function cborEncodeCanonicalHex(value: CborValue): Hex {
  return bytesToHex(cborEncodeCanonical(value));
}

/* -------------------------------- Decode ----------------------------------- */

/** Decode CBOR from bytes | 0x-hex | UTF-8 string into a JS value. */
export function cborDecode(input: Uint8Array | Hex | string): CborValue {
  const bytes = toBytes(input);
  return cborg.decode(bytes) as CborValue;
}

/* ------------------------------- Utilities --------------------------------- */

/**
 * Validate that a value can be encoded to canonical CBOR and decoded back
 * losslessly. Returns the round-tripped value (useful for normalization).
 */
export function cborNormalize(value: CborValue): CborValue {
  return cborDecode(cborEncodeCanonical(value));
}

export const CBOR = {
  encode: cborEncode,
  encodeHex: cborEncodeHex,
  encodeCanonical: cborEncodeCanonical,
  encodeCanonicalHex: cborEncodeCanonicalHex,
  decode: cborDecode,
  normalize: cborNormalize,
} as const;
