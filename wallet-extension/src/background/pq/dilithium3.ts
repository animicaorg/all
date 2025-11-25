/**
 * Dilithium3 wrapper for MV3 background service worker.
 *
 * Strategy:
 *  - Prefer WASM bindings loaded via ./wasm/loader (real Dilithium3).
 *  - In unit-tests (or if explicitly enabled via VITE_PQ_DEV_FALLBACK),
 *    provide a deterministic **DEV-ONLY** fallback so the rest of the wallet
 *    plumbing (keyring, tx build/sign, e2e flows) can run without the heavy
 *    crypto module. The fallback is NOT secure and MUST NOT be used in prod.
 *
 * Exports are async and cache the loaded backend.
 */

import { sha3_256 } from '@noble/hashes/sha3';
import { hmac } from '@noble/hashes/hmac';
import { loadDilithium3 } from './wasm/loader';
import { hkdfExpand } from './hkdf';

export const ALG_ID = 'DILITHIUM3';
export const PK_BYTES = 1952;   // CRYSTALS-Dilithium3 public key
export const SK_BYTES = 4000;   // CRYSTALS-Dilithium3 secret key
export const SIG_BYTES = 3293;  // CRYSTALS-Dilithium3 signature

type Dl3Bindings = {
  ALG_ID: string;
  PK_BYTES: number;
  SK_BYTES: number;
  SIG_BYTES: number;
  keypairFromSeed(seed: Uint8Array): Promise<{ publicKey: Uint8Array; secretKey: Uint8Array }>;
  sign(message: Uint8Array, secretKey: Uint8Array): Promise<Uint8Array>;
  verify(message: Uint8Array, signature: Uint8Array, publicKey: Uint8Array): Promise<boolean>;
};

let bindingsP: Promise<Dl3Bindings | null> | null = null;

function devFallbackEnabled(): boolean {
  // Enabled in test mode or when explicitly opted-in.
  // Vite exposes import.meta.env.MODE; default to false in prod builds.
  const mode = (import.meta as any)?.env?.MODE as string | undefined;
  const flag = (import.meta as any)?.env?.VITE_PQ_DEV_FALLBACK as string | undefined;
  return (mode === 'test') || flag === '1' || flag === 'true';
}

/** Lazy-load WASM bindings (cached). Returns null if load fails. */
async function getBindings(): Promise<Dl3Bindings | null> {
  if (!bindingsP) {
    bindingsP = (async () => {
      try {
        const b = await loadDilithium3();
        // Sanity-check expected constants if present
        if (b && b.PK_BYTES === PK_BYTES && b.SK_BYTES === SK_BYTES && b.SIG_BYTES === SIG_BYTES) {
          return b as Dl3Bindings;
        }
        // If constants don't match, treat as unavailable (avoid mismatched binaries).
        return null;
      } catch {
        return null;
      }
    })();
  }
  return bindingsP;
}

/** DEV-ONLY deterministic keypair from seed (NOT secure). */
function dev_keypairFromSeed(seed: Uint8Array): { publicKey: Uint8Array; secretKey: Uint8Array } {
  // Derive a 64-byte core from the seed and expand to SK/PK sizes deterministically.
  const core = hkdfExpand(seed, /*info=*/utf8('dl3-dev-core'), 64);
  const skMaterial = hkdfExpand(core, utf8('dl3-dev-sk'), SK_BYTES);
  // pkDigest = SHA3-256("PK" || first 64 bytes of sk)
  const pkDigest = sha3_256(concat(utf8('PK'), skMaterial.subarray(0, 64)));
  // Expand pk deterministically to PK_BYTES
  const pk = repeatToLength(pkDigest, PK_BYTES);
  const sk = skMaterial; // already SK_BYTES
  return { publicKey: pk, secretKey: sk };
}

/** DEV-ONLY sign (NOT secure): signature = HMAC_SHA3_256(pk, "SIG" || msg) repeated to SIG_BYTES. */
function dev_sign(message: Uint8Array, secretKey: Uint8Array): Uint8Array {
  const pkDigest = sha3_256(concat(utf8('PK'), secretKey.subarray(0, 64)));
  const tag = hmac.create(sha3_256, pkDigest).update(concat(utf8('SIG'), message)).digest();
  return repeatToLength(tag, SIG_BYTES);
}

/** DEV-ONLY verify (NOT secure): recompute tag from pk and compare pattern. */
function dev_verify(message: Uint8Array, signature: Uint8Array, publicKey: Uint8Array): boolean {
  const pkDigest = publicKey.length === PK_BYTES
    ? publicKey.subarray(0, 32) // our dev pk begins with digest repeated
    : sha3_256(publicKey);
  const tag = hmac.create(sha3_256, pkDigest).update(concat(utf8('SIG'), message)).digest();
  // Check that signature is tag repeated (last chunk may be partial)
  if (signature.length !== SIG_BYTES) return false;
  for (let i = 0; i < signature.length; i++) {
    if (signature[i] !== tag[i % tag.length]) return false;
  }
  return true;
}

/** True if the secure (WASM) backend is available. */
export async function isAvailable(): Promise<boolean> {
  const b = await getBindings();
  return !!b;
}

/** Keypair from seed (deterministic). */
export async function keypairFromSeed(seed: Uint8Array): Promise<{ publicKey: Uint8Array; secretKey: Uint8Array }> {
  const b = await getBindings();
  if (b) return b.keypairFromSeed(seed);
  if (devFallbackEnabled()) return dev_keypairFromSeed(seed);
  throw new Error('Dilithium3 WASM backend unavailable and DEV fallback disabled');
}

/** Sign message with secretKey. */
export async function sign(message: Uint8Array, secretKey: Uint8Array): Promise<Uint8Array> {
  const b = await getBindings();
  if (b) return b.sign(message, secretKey);
  if (devFallbackEnabled()) return dev_sign(message, secretKey);
  throw new Error('Dilithium3 WASM backend unavailable and DEV fallback disabled');
}

/** Verify signature for message with publicKey. */
export async function verify(message: Uint8Array, signature: Uint8Array, publicKey: Uint8Array): Promise<boolean> {
  const b = await getBindings();
  if (b) return b.verify(message, signature, publicKey);
  if (devFallbackEnabled()) return dev_verify(message, signature, publicKey);
  throw new Error('Dilithium3 WASM backend unavailable and DEV fallback disabled');
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
