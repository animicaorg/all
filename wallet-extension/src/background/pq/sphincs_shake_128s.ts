/**
 * SPHINCS+ SHAKE-128s wrapper for MV3 background service worker.
 *
 * Strategy:
 *  - Prefer WASM bindings loaded via ./wasm/loader (real SPHINCS+ SHAKE-128s).
 *  - In unit-tests (or if explicitly enabled via VITE_PQ_DEV_FALLBACK),
 *    provide a deterministic **DEV-ONLY** fallback so higher-level plumbing
 *    (keyring, tx sign, e2e) can run without heavy crypto. The fallback is
 *    NOT secure and MUST NOT be used in production.
 *
 * Exports are async and cache the loaded backend.
 */

import { sha3_256 } from '../../polyfills/noble/sha3.ts';
import { hmac } from '../../polyfills/noble/hmac.ts';
import { loadSphincsShake128s } from './wasm/loader';
import { hkdfExpand } from './hkdf';

export const ALG_ID = 'SPHINCS+-SHAKE-128s';
// Canonical sizes for SPHINCS+-SHAKE-128s (NIST PQC Round 3 parameters).
// These constants are validated against the WASM module when available.
export const PK_BYTES = 32;      // public key bytes
export const SK_BYTES = 64;      // secret key bytes
export const SIG_BYTES = 7856;   // signature bytes

type SphincsBindings = {
  ALG_ID: string;
  PK_BYTES: number;
  SK_BYTES: number;
  SIG_BYTES: number;
  keypairFromSeed(seed: Uint8Array): Promise<{ publicKey: Uint8Array; secretKey: Uint8Array }>;
  sign(message: Uint8Array, secretKey: Uint8Array): Promise<Uint8Array>;
  verify(message: Uint8Array, signature: Uint8Array, publicKey: Uint8Array): Promise<boolean>;
};

let bindingsP: Promise<SphincsBindings | null> | null = null;

function devFallbackEnabled(): boolean {
  const mode = (import.meta as any)?.env?.MODE as string | undefined;
  const flag = (import.meta as any)?.env?.VITE_PQ_DEV_FALLBACK as string | undefined;
  return (mode === 'test') || flag === '1' || flag === 'true';
}

/** Lazy-load WASM bindings (cached). Returns null if load fails or mismatched. */
async function getBindings(): Promise<SphincsBindings | null> {
  if (!bindingsP) {
    bindingsP = (async () => {
      try {
        const b = await loadSphincsShake128s();
        if (
          b &&
          b.PK_BYTES === PK_BYTES &&
          b.SK_BYTES === SK_BYTES &&
          b.SIG_BYTES === SIG_BYTES
        ) {
          return b as SphincsBindings;
        }
        return null;
      } catch {
        return null;
      }
    })();
  }
  return bindingsP;
}

/* ---------------------------- DEV FALLBACK ---------------------------- */
/** DEV-ONLY deterministic keypair from seed (NOT secure). */
function dev_keypairFromSeed(seed: Uint8Array): { publicKey: Uint8Array; secretKey: Uint8Array } {
  // Use HKDF to derive deterministic SK and PK material.
  const core = hkdfExpand(seed, utf8('sphincs-dev-core'), 64);
  const sk = hkdfExpand(core, utf8('sphincs-dev-sk'), SK_BYTES);
  // Derive a compact pk digest from sk so we can reconstruct in verify() reliably.
  const pkDigest = sha3_256(concat(utf8('PK'), sk.subarray(0, 48)));
  const pk = repeatToLength(pkDigest, PK_BYTES);
  return { publicKey: pk, secretKey: sk };
}

/** DEV-ONLY sign (NOT secure): HMAC-SHA3-256(pkDigest, "SIG"||msg) repeated to SIG_BYTES. */
function dev_sign(message: Uint8Array, secretKey: Uint8Array): Uint8Array {
  const pkDigest = sha3_256(concat(utf8('PK'), secretKey.subarray(0, 48)));
  const tag = hmac.create(sha3_256, pkDigest).update(concat(utf8('SIG'), message)).digest();
  return repeatToLength(tag, SIG_BYTES);
}

/** DEV-ONLY verify (NOT secure): recompute tag from publicKey and compare repeated pattern. */
function dev_verify(message: Uint8Array, signature: Uint8Array, publicKey: Uint8Array): boolean {
  if (signature.length !== SIG_BYTES) return false;
  const pkDigest = publicKey.length >= 32 ? publicKey.subarray(0, 32) : sha3_256(publicKey);
  const tag = hmac.create(sha3_256, pkDigest).update(concat(utf8('SIG'), message)).digest();
  for (let i = 0; i < signature.length; i++) {
    if (signature[i] !== tag[i % tag.length]) return false;
  }
  return true;
}

/* ------------------------------ API ------------------------------ */

export async function isAvailable(): Promise<boolean> {
  return !!(await getBindings());
}

export async function keypairFromSeed(seed: Uint8Array): Promise<{ publicKey: Uint8Array; secretKey: Uint8Array }> {
  const b = await getBindings();
  if (b) return b.keypairFromSeed(seed);
  if (devFallbackEnabled()) return dev_keypairFromSeed(seed);
  throw new Error('SPHINCS+ WASM backend unavailable and DEV fallback disabled');
}

export async function sign(message: Uint8Array, secretKey: Uint8Array): Promise<Uint8Array> {
  const b = await getBindings();
  if (b) return b.sign(message, secretKey);
  if (devFallbackEnabled()) return dev_sign(message, secretKey);
  throw new Error('SPHINCS+ WASM backend unavailable and DEV fallback disabled');
}

export async function verify(message: Uint8Array, signature: Uint8Array, publicKey: Uint8Array): Promise<boolean> {
  const b = await getBindings();
  if (b) return b.verify(message, signature, publicKey);
  if (devFallbackEnabled()) return dev_verify(message, signature, publicKey);
  throw new Error('SPHINCS+ WASM backend unavailable and DEV fallback disabled');
}

/** Optional: pre-load backend early to avoid first-use latency. */
export async function ensureLoaded(): Promise<void> {
  await getBindings();
}

/* ------------------------------ helpers ------------------------------ */

function utf8(s: string): Uint8Array {
  return new TextEncoder().encode(s);
}

function concat(a: Uint8Array, b: Uint8Array): Uint8Array {
  const out = new Uint8Array(a.length + b.length);
  out.set(a, 0);
  out.set(b, a.length);
  return out;
}

function repeatToLength(block: Uint8Array, totalLen: number): Uint8Array {
  const out = new Uint8Array(totalLen);
  let off = 0;
  while (off < totalLen) {
    const take = Math.min(block.length, totalLen - off);
    out.set(block.subarray(0, take), off);
    off += take;
  }
  return out;
}
