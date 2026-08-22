/**
 * Hierarchical-deterministic key derivation for Animica (ANM).
 *
 * Animica accounts are ML-DSA-65 (FIPS 204) keypairs. FIPS 204 key generation is
 * deterministic from a 32-byte seed ξ, so an HD wallet only has to derive ξ.
 *
 * Scheme (see docs/wallet/HD_DERIVATION.md):
 *
 *   1. BIP-39 mnemonic  → 64-byte seed (PBKDF2-HMAC-SHA512, 2048 rounds, salt "mnemonic" + passphrase)
 *   2. SLIP-0010 ed25519-style hardened derivation (HMAC-SHA512, key "ed25519 seed")
 *      along   m / 44' / 4279885' / account' / 0' / index'
 *      4279885 = 0x414E4D = ASCII "ANM" — the SLIP-0044 coin type registered for Animica.
 *   3. The 32-byte private-key half of the final node IS the ML-DSA-65 seed ξ:
 *        (pk, sk) = ML-DSA-65.KeyGen_internal(ξ)      // e.g. @noble/post-quantum ml_dsa65.keygen(ξ)
 *        address  = bech32m("anim", 0x1003 || sha3_256(pk))  // see ./address.ts
 *
 * Every level is hardened (SLIP-0010 only defines hardened children for the
 * ed25519 curve), so the derivation never uses elliptic-curve arithmetic at all —
 * it is pure HMAC-SHA512, which is why it can be reused unchanged for a
 * post-quantum scheme.
 *
 * This module deliberately stops at the seed: ML-DSA-65 key generation lives in
 * the wallet (browser extension / mobile app / third-party wallet), not here.
 */

import { hmac } from '@noble/hashes/hmac';
import { sha512 } from '@noble/hashes/sha512';
import { pbkdf2 } from '@noble/hashes/pbkdf2';

/** SLIP-0044 coin type for Animica: 0x414E4D = ASCII "ANM". */
export const ANIMICA_COIN_TYPE = 4279885;

/** BIP-44 purpose. */
export const BIP44_PURPOSE = 44;

/** Hardened-index offset (2^31). */
export const HARDENED_OFFSET = 0x80000000;

/** SLIP-0010 master-key HMAC key for the ed25519 derivation family. */
const ED25519_SEED_KEY = new TextEncoder().encode('ed25519 seed');

export interface HDNode {
  /** 32-byte private key half (for Animica: the ML-DSA-65 seed ξ). */
  key: Uint8Array;
  /** 32-byte chain code. */
  chainCode: Uint8Array;
}

function ser32(i: number): Uint8Array {
  const out = new Uint8Array(4);
  out[0] = (i >>> 24) & 0xff;
  out[1] = (i >>> 16) & 0xff;
  out[2] = (i >>> 8) & 0xff;
  out[3] = i & 0xff;
  return out;
}

function concat(...parts: Uint8Array[]): Uint8Array {
  const len = parts.reduce((n, p) => n + p.length, 0);
  const out = new Uint8Array(len);
  let off = 0;
  for (const p of parts) {
    out.set(p, off);
    off += p.length;
  }
  return out;
}

/**
 * BIP-39: mnemonic sentence → 64-byte seed.
 * Wordlist/checksum validation is intentionally out of scope (use @scure/bip39 for that);
 * this only performs the standard PBKDF2 stretch so the package stays dependency-free.
 */
export function mnemonicToSeed(mnemonic: string, passphrase = ''): Uint8Array {
  const enc = new TextEncoder();
  const normalized = mnemonic.normalize('NFKD').trim().split(/\s+/u).join(' ');
  return pbkdf2(sha512, enc.encode(normalized), enc.encode('mnemonic' + passphrase.normalize('NFKD')), {
    c: 2048,
    dkLen: 64,
  });
}

/** SLIP-0010 master node (ed25519 family) from a BIP-39 seed. */
export function masterNodeFromSeed(seed: Uint8Array): HDNode {
  if (seed.length < 16 || seed.length > 64) {
    throw new Error(`HD seed must be 16..64 bytes, got ${seed.length}`);
  }
  const I = hmac(sha512, ED25519_SEED_KEY, seed);
  return { key: I.slice(0, 32), chainCode: I.slice(32, 64) };
}

/** SLIP-0010 hardened child. `index` is the raw (un-hardened) index; it is hardened here. */
export function deriveHardenedChild(parent: HDNode, index: number): HDNode {
  if (!Number.isInteger(index) || index < 0 || index >= HARDENED_OFFSET) {
    throw new Error(`child index must be an integer in [0, 2^31), got ${index}`);
  }
  const i = (index + HARDENED_OFFSET) >>> 0;
  const data = concat(new Uint8Array([0x00]), parent.key, ser32(i));
  const I = hmac(sha512, parent.chainCode, data);
  return { key: I.slice(0, 32), chainCode: I.slice(32, 64) };
}

/**
 * Parse a BIP-32 path string such as `m/44'/4279885'/0'/0'/0'`.
 * Accepts `'` or `h` or `H` as the hardened marker. Every level MUST be hardened.
 */
export function parsePath(path: string): number[] {
  const parts = path.trim().split('/');
  if (parts[0] !== 'm') throw new Error(`path must start with "m": ${path}`);
  return parts.slice(1).map((p) => {
    const m = /^(\d+)(['hH])$/u.exec(p);
    if (!m) throw new Error(`every Animica path level must be hardened (e.g. 44'): bad segment "${p}" in ${path}`);
    const n = Number(m[1]);
    if (n >= HARDENED_OFFSET) throw new Error(`index out of range: ${p}`);
    return n;
  });
}

/** Derive the node at an arbitrary all-hardened path from a BIP-39 seed. */
export function deriveNodeFromSeed(seed: Uint8Array, path: string | number[]): HDNode {
  const levels = typeof path === 'string' ? parsePath(path) : path;
  let node = masterNodeFromSeed(seed);
  for (const idx of levels) node = deriveHardenedChild(node, idx);
  return node;
}

/** Canonical Animica BIP-44 path for (account, index). */
export function animicaPath(account = 0, index = 0): string {
  return `m/${BIP44_PURPOSE}'/${ANIMICA_COIN_TYPE}'/${account}'/0'/${index}'`;
}

/**
 * Derive the 32-byte ML-DSA-65 seed ξ for an Animica account from a BIP-39 seed.
 * Feed the result to `ml_dsa65.keygen(seed)` (@noble/post-quantum) or any FIPS 204
 * `ML-DSA.KeyGen_internal(ξ)` to obtain the keypair, then `addressFromPubkey(pk, 0x1003)`.
 */
export function deriveAnimicaSeed(seed: Uint8Array, account = 0, index = 0): Uint8Array {
  return deriveNodeFromSeed(seed, animicaPath(account, index)).key;
}

/** Convenience: mnemonic → ML-DSA-65 seed ξ in one call. */
export function deriveAnimicaSeedFromMnemonic(
  mnemonic: string,
  account = 0,
  index = 0,
  passphrase = '',
): Uint8Array {
  return deriveAnimicaSeed(mnemonicToSeed(mnemonic, passphrase), account, index);
}
