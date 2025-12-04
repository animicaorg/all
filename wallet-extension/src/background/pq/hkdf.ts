/**
 * HKDF-SHA3-256 (RFC 5869-style) — Extract-and-Expand with HMAC(SHA3-256).
 *
 * We use @noble/hashes for a small, audited, browser-safe implementation.
 * This module is MV3-safe (no Node builtins) and works in workers & service-workers.
 *
 * Usage:
 *   const okm = await hkdf({ ikm, salt, info, length: 32 });
 */

import { hmac } from '../../polyfills/noble/hmac.ts';
import { sha3_256, sha3_512 } from '../../polyfills/noble/sha3.ts';

export interface HKDFOpts {
  /** Input keying material (secret). */
  ikm: Uint8Array;
  /** Optional salt (non-secret); if not provided, a zero-filled hashLen is used. */
  salt?: Uint8Array;
  /** Optional context/application-specific info. */
  info?: Uint8Array;
  /** Desired length of output keying material in bytes. */
  length: number;
}

/** Hash output length for SHA3-256 in bytes. */
const HASH_LEN = 32;

/** Coerce various byte-like inputs to Uint8Array. */
function toU8(x?: Uint8Array | ArrayBuffer | number[] | null): Uint8Array {
  if (!x) return new Uint8Array();
  if (typeof x === 'string') return new TextEncoder().encode(x);
  if (x instanceof Uint8Array) return x;
  if (x instanceof ArrayBuffer) return new Uint8Array(x);
  if (Array.isArray(x)) return new Uint8Array(x);
  // @ts-ignore Buffer in some environments; MV3 won't have it.
  if (typeof Buffer !== 'undefined' && Buffer.isBuffer?.(x)) {
    // @ts-ignore
    return new Uint8Array(x.buffer, x.byteOffset, x.byteLength);
  }
  // Last-resort: try to coerce array-like objects
  if (typeof (x as any).length === 'number') return Uint8Array.from(x as any);
  throw new Error('Unsupported bytes-like input');
}

/** Concatenate two Uint8Arrays without mutating inputs. */
function concat(a: Uint8Array, b: Uint8Array): Uint8Array {
  const out = new Uint8Array(a.length + b.length);
  out.set(a, 0);
  out.set(b, a.length);
  return out;
}

/** HKDF-Extract(salt, IKM) -> PRK */
function extract(salt: Uint8Array, ikm: Uint8Array): Uint8Array {
  const mac = hmac.create(sha3_256, salt);
  mac.update(ikm);
  return mac.digest(); // PRK
}

/** HKDF-Expand(PRK, info, L) -> OKM */
function expand(prk: Uint8Array, info: Uint8Array, length: number): Uint8Array {
  const n = Math.ceil(length / HASH_LEN);
  if (n > 255) throw new Error('HKDF length too large (requires >255 blocks)');

  let t = new Uint8Array(0);
  const okm = new Uint8Array(length);
  let pos = 0;

  for (let i = 1; i <= n; i++) {
    const mac = hmac.create(sha3_256, prk);
    mac.update(t);
    mac.update(info);
    mac.update(Uint8Array.of(i));
    t = mac.digest();

    const take = Math.min(HASH_LEN, length - pos);
    okm.set(t.subarray(0, take), pos);
    pos += take;
  }
  return okm;
}

/**
 * Derive keying material deterministically using HKDF-SHA3-256.
 */
export async function hkdf(opts: HKDFOpts): Promise<Uint8Array> {
  const ikm = toU8(opts.ikm);
  if (!(ikm instanceof Uint8Array) || ikm.length === 0) {
    throw new Error('hkdf: ikm must be a non-empty Uint8Array');
  }
  const L = opts.length >>> 0;
  if (!Number.isFinite(L) || L <= 0) throw new Error('hkdf: length must be > 0');

  const salt = opts.salt ? toU8(opts.salt) : new Uint8Array(HASH_LEN); // zeros if absent
  const info = opts.info ? toU8(opts.info) : new Uint8Array(0);

  const prk = extract(salt, ikm);
  const okm = expand(prk, info, L);
  // Zero sensitive intermediates (best-effort)
  prk.fill(0);
  return okm;
}

/** Convenience alias matching the keyring/vault helpers. */
export async function hkdfSha3_256(opts: HKDFOpts): Promise<Uint8Array> {
  return hkdf(opts);
}

/** Minimal HMAC-SHA3-512 helper used by PBKDF2 and vault derivation. */
export async function hmacSha3_512(key: Uint8Array, data: Uint8Array): Promise<Uint8Array> {
  return hmac(sha3_512, key, data);
}

// Re-export sha3_256 for convenience (used by mnemonic/vault helpers)
export { sha3_256 };

/** Convenience helper to derive hex string output (lowercase). */
export async function hkdfHex(opts: HKDFOpts): Promise<string> {
  const u8 = await hkdf(opts);
  let s = '';
  for (let i = 0; i < u8.length; i++) s += u8[i].toString(16).padStart(2, '0');
  return s;
}

/**
 * Synchronous HKDF expand helper for small deterministic derivations.
 *
 * Uses a zero salt (per RFC 5869 recommendation) unless provided. This is
 * intentionally synchronous for use in lightweight dev-only fallbacks where
 * async/await is noisy and extract/expand operations are inexpensive.
 */
export function hkdfExpand(
  ikm: Uint8Array | ArrayBuffer | number[],
  info: Uint8Array | ArrayBuffer | number[] = new Uint8Array(0),
  length: number,
  salt?: Uint8Array | ArrayBuffer | number[],
): Uint8Array {
  const ikmU8 = toU8(ikm);
  if (ikmU8.length === 0) throw new Error('hkdfExpand: ikm must be non-empty');

  const L = length >>> 0;
  if (!Number.isFinite(L) || L <= 0) throw new Error('hkdfExpand: length must be > 0');

  const saltU8 = salt ? toU8(salt) : new Uint8Array(HASH_LEN);
  const infoU8 = info ? toU8(info) : new Uint8Array(0);

  const prk = extract(saltU8, ikmU8);
  const okm = expand(prk, infoU8, L);
  prk.fill(0);
  return okm;
}

export const _internal = { HASH_LEN, extract, expand, toU8, concat };
