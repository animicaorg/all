/**
 * Mnemonic utilities (BIP-39-like) for @animica/sdk.
 *
 * - Uses the standard English wordlist for generation/validation.
 * - Derives a seed with HKDF-SHA3-256 (NOT BIP-39's PBKDF2-HMAC-SHA512).
 * - NFC/NFKD normalization consistent with BIP-39 to avoid surprises.
 *
 * Rationale:
 *   Animica wallets and SDKs prefer SHA3-family primitives. We keep
 *   compatibility with the common 12/15/18/21/24-word mnemonics while
 *   switching the seed derivation to HKDF(SHA3-256).
 */

import { generateMnemonic as _gen, validateMnemonic as _validate, mnemonicToEntropy } from '@scure/bip39'
import { wordlist } from '@scure/bip39/wordlists/english'
import { hkdf } from '@noble/hashes/hkdf'
import { sha3_256 } from '@noble/hashes/sha3'
import { utf8ToBytes } from '../utils/bytes'

/** Allowed entropy strengths (bits) per BIP-39. */
export type MnemonicStrength = 128 | 160 | 192 | 224 | 256

export interface SeedOptions {
  /**
   * Optional passphrase mixed into the salt (normalized NFKD).
   * Default: '' (empty).
   */
  passphrase?: string
  /**
   * Output length in bytes for HKDF expand phase.
   * Default: 32 bytes.
   */
  length?: number
  /**
   * Application salt prefix. You almost never need to change this.
   * Default: 'animica mnemonic'.
   */
  saltPrefix?: string
  /**
   * HKDF "info" context string to domain-separate different uses.
   * Default: 'omni-sdk key derivation v1'
   */
  info?: string
}

/**
 * Generate a valid mnemonic (default 24 words = 256-bit entropy).
 */
export function generateMnemonic(strength: MnemonicStrength = 256): string {
  return _gen(wordlist, strength)
}

/**
 * Validate a mnemonic against the English wordlist and checksum.
 */
export function validateMnemonic(mnemonic: string): boolean {
  try {
    return _validate(normalizeNFKD(mnemonic), wordlist)
  } catch {
    return false
  }
}

/**
 * Convert mnemonic to raw seed bytes using HKDF(SHA3-256).
 *
 * This intentionally differs from BIP-39's PBKDF2-HMAC-SHA512. We favor SHA3
 * and HKDF to match the rest of the stack (P2P handshake, PQ tools). The
 * mnemonic itself remains BIP-39 compatible.
 */
export function mnemonicToSeed(
  mnemonic: string,
  opts: SeedOptions = {}
): Uint8Array {
  const m = normalizeNFKD(mnemonic)
  if (!_validate(m, wordlist)) {
    throw new Error('Invalid mnemonic: checksum or wordlist mismatch')
  }

  const passphrase = normalizeNFKD(opts.passphrase ?? '')
  const saltPrefix = opts.saltPrefix ?? 'animica mnemonic'
  const info = opts.info ?? 'omni-sdk key derivation v1'
  const length = opts.length ?? 32

  // ikm = UTF-8 bytes of mnemonic
  const ikm = utf8ToBytes(m)
  // salt = "animica mnemonic" || passphrase
  const salt = utf8ToBytes(saltPrefix + passphrase)
  const infoBytes = utf8ToBytes(info)

  // HKDF-Extract + Expand (SHA3-256)
  const okm = hkdf(sha3_256, ikm, salt, infoBytes, length)
  return okm
}

/**
 * (Optional) Exported helper to obtain the underlying entropy from a mnemonic.
 * Can be used to implement custom derivation schemes deterministically.
 */
export function mnemonicToRawEntropy(mnemonic: string): Uint8Array {
  const m = normalizeNFKD(mnemonic)
  // @scure/bip39 returns hex string; convert to bytes.
  const hex = mnemonicToEntropy(m, wordlist)
  const out = new Uint8Array(hex.length / 2)
  for (let i = 0; i < out.length; i++) {
    out[i] = parseInt(hex.slice(i * 2, i * 2 + 2), 16)
  }
  return out
}

// ──────────────────────────────────────────────────────────────────────────────
// Internals
// ──────────────────────────────────────────────────────────────────────────────

function normalizeNFKD(s: string): string {
  // BIP-39 requires NFKD normalization for both mnemonic and passphrase.
  try {
    return s.normalize('NFKD')
  } catch {
    // In rare JS runtimes without ICU, return as-is.
    return s
  }
}

export default {
  generateMnemonic,
  validateMnemonic,
  mnemonicToSeed,
  mnemonicToRawEntropy
}
