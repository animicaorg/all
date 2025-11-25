/**
 * @file cbor.ts
 * Deterministic CBOR helpers used across the TypeScript SDK.
 *
 * - Uses the `cborg` encoder/decoder (RFC 7049 canonical map key ordering).
 * - Fixed-length encoding only on encode (no indefinite strings/bytes/maps/arrays).
 * - Strict decoding (rejects indefinite forms, NaN/Infinity/undefined unless explicitly allowed).
 * - Safe round-trippable primitives for SignBytes / transaction & header payloads.
 *
 * Notes on integers & BigInt:
 * - JavaScript `number`s are used for values within the IEEE 754 safe range.
 * - For larger integers, pass a `bigint`. `cborg` encodes BigInt up to 64-bit major 0/1;
 *   beyond that you should represent large values as byte strings in your schema if you
 *   require cross-implementation stability without tags.
 */

import { encode as cborgEncode, decode as cborgDecode } from 'cborg'

/** Encode value to CBOR using canonical, deterministic settings. */
export function encodeCanonical(value: unknown): Uint8Array {
  // cborg defaults:
  // - canonical map key ordering (RFC 7049 length-first, then bytewise)
  // - fixed-length items only for encode (no indefinite)
  // - minimal integer & float forms
  return cborgEncode(value /* options left default on purpose */)
}

export interface DecodeStrictOptions {
  /** If true, allow decoding `undefined` tokens; default false coerces them to error. */
  allowUndefined?: boolean
  /** If true, allow NaN; default false rejects NaN. */
  allowNaN?: boolean
  /** If true, allow Infinity/-Infinity; default false rejects them. */
  allowInfinity?: boolean
}

/**
 * Strictly decode CBOR bytes. By default this:
 *  - Rejects indefinite-length entities
 *  - Rejects `undefined`, `NaN`, and `Infinity`
 *  - Enforces minimal integer/length encodings (`strict: true`)
 */
export function decodeStrict(data: BytesLike, opts: DecodeStrictOptions = {}): unknown {
  const bytes = toBytes(data)
  return cborgDecode(bytes, {
    allowIndefinite: false,
    allowUndefined: opts.allowUndefined ?? false,
    allowNaN: opts.allowNaN ?? false,
    allowInfinity: opts.allowInfinity ?? false,
    allowBigInt: true,
    strict: true
  } as any)
}

/** Convenience: encode to hex string (0x-prefixed). */
export function encodeToHex(value: unknown): string {
  return toHex(encodeCanonical(value))
}

/** Convenience: decode from a hex string (0x… or bare hex). */
export function decodeFromHex(hex: string, opts?: DecodeStrictOptions): unknown {
  return decodeStrict(fromHex(hex), opts)
}

/** Bytes-like accepted by helpers. */
export type BytesLike = Uint8Array | ArrayBuffer | string

/** Internal: normalize BytesLike to Uint8Array. */
function toBytes(data: BytesLike): Uint8Array {
  if (data instanceof Uint8Array) return data
  if (typeof ArrayBuffer !== 'undefined' && data instanceof ArrayBuffer) {
    return new Uint8Array(data)
  }
  if (typeof data === 'string') {
    // Treat string as hex; tolerate 0x prefix and odd-length
    return fromHex(data)
  }
  throw new TypeError('Unsupported BytesLike input')
}

/** Hex → bytes (tolerates 0x prefix). */
export function fromHex(hex: string): Uint8Array {
  let s = hex.trim().toLowerCase()
  if (s.startsWith('0x')) s = s.slice(2)
  if (s.length === 0) return new Uint8Array()
  if (s.length % 2 === 1) s = '0' + s
  const out = new Uint8Array(s.length / 2)
  for (let i = 0; i < out.length; i++) {
    const byte = s.slice(i * 2, i * 2 + 2)
    const v = Number.parseInt(byte, 16)
    if (Number.isNaN(v)) throw new Error(`Invalid hex at byte ${i}: "${byte}"`)
    out[i] = v
  }
  return out
}

/** bytes → 0x-hex */
export function toHex(bytes: Uint8Array): string {
  const lut = Array.from({ length: 256 }, (_, i) => i.toString(16).padStart(2, '0'))
  let out = '0x'
  for (let i = 0; i < bytes.length; i++) out += lut[bytes[i]]
  return out
}

/**
 * Deep-freeze a value intended for canonical encoding to guard accidental mutation
 * prior to hashing/signing. No-ops on primitives; returns the same reference for arrays/objects.
 */
export function deepFreeze<T>(value: T): T {
  if (value && typeof value === 'object') {
    Object.freeze(value)
    for (const v of Object.values(value as any)) deepFreeze(v)
  }
  return value
}

export default {
  encodeCanonical,
  decodeStrict,
  encodeToHex,
  decodeFromHex,
  fromHex,
  toHex,
  deepFreeze
}
