/**
 * Hash utilities (browser & Node) built on @noble/hashes.
 *
 * Exposes:
 *  - sha3_256 / sha3_512
 *  - keccak256 / keccak512
 *  - digestHex helpers
 *  - concat hashing helpers
 *
 * All functions accept BytesLike and return Uint8Array unless otherwise stated.
 */

import { sha3_256 as _sha3_256, sha3_512 as _sha3_512 } from '@noble/hashes/sha3'
import { keccak_256 as _keccak256, keccak_512 as _keccak512 } from '@noble/hashes/keccak'
import type { BytesLike } from './bytes'
import { toBytes, concatBytes, bytesToHex } from './bytes'

export type HashFn = (input: Uint8Array) => Uint8Array

/** SHA3-256 digest of input bytes. */
export function sha3_256(data: BytesLike): Uint8Array {
  return _sha3_256(toBytes(data))
}

/** SHA3-512 digest of input bytes. */
export function sha3_512(data: BytesLike): Uint8Array {
  return _sha3_512(toBytes(data))
}

/** Keccak-256 digest of input bytes. (Ethereum-style Keccak) */
export function keccak256(data: BytesLike): Uint8Array {
  return _keccak256(toBytes(data))
}

/** Keccak-512 digest of input bytes. */
export function keccak512(data: BytesLike): Uint8Array {
  return _keccak512(toBytes(data))
}

/** Generic digest helper with a selected hash function. */
export function digest(fn: HashFn, data: BytesLike): Uint8Array {
  return fn(toBytes(data))
}

/** Digest and return 0x-prefixed lowercase hex string. */
export function digestHex(fn: HashFn, data: BytesLike): string {
  return bytesToHex(digest(fn, data), true)
}

/** Hash the concatenation of multiple byte-like chunks. */
export function hashConcat(fn: HashFn, parts: BytesLike[]): Uint8Array {
  return fn(concatBytes(parts))
}

/** Hex variant of hashConcat. */
export function hashConcatHex(fn: HashFn, parts: BytesLike[]): string {
  return bytesToHex(hashConcat(fn, parts), true)
}

/**
 * Simple tagged-hash helper: fn( tag || 0x00 || payload ).
 * This is a generic domain-separation convenience for *client-side* usage.
 * For consensus-critical tagging, always follow the chain's canonical rules.
 */
export function taggedHash(
  fn: HashFn,
  tagUtf8: string,
  payload: BytesLike
): Uint8Array {
  const tagBytes = new TextEncoder().encode(tagUtf8)
  const sep = new Uint8Array([0x00])
  return fn(concatBytes([tagBytes, sep, toBytes(payload)]))
}

/** Hex variant of taggedHash. */
export function taggedHashHex(
  fn: HashFn,
  tagUtf8: string,
  payload: BytesLike
): string {
  return bytesToHex(taggedHash(fn, tagUtf8, payload), true)
}

// Named hex convenience wrappers (common in RPC / logging)
export const sha3_256_hex = (data: BytesLike) => digestHex(_sha3_256, data)
export const sha3_512_hex = (data: BytesLike) => digestHex(_sha3_512, data)
export const keccak256_hex = (data: BytesLike) => digestHex(_keccak256, data)
export const keccak512_hex = (data: BytesLike) => digestHex(_keccak512, data)
