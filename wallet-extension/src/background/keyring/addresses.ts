/**
 * Addressing (bech32m, anim1...)
 *
 * Format (payload bytes, then converted to 5-bit words and encoded with bech32m):
 *   payload = alg_id (2 bytes, big-endian) || sha3_256(pubkey_bytes) (32 bytes)
 *   address = bech32m_encode(hrp="anim", payload)
 *
 * Notes
 * - We use SHA3-256 over the raw PQ public key bytes.
 * - alg_id (2 bytes) distinguishes the signing scheme used by the account.
 * - HRP can be overridden by network config (e.g., "animt" for testnets).
 *
 * This module intentionally avoids storing any secret material.
 */

import type { KeyAlgo } from './storage';
import { toWords, fromWords, encodeBech32m, decodeBech32m } from '../../utils/bech32';
import { sha3_256 as sha3Fast } from '../../polyfills/noble/sha3.ts';

/** Default human-readable part (HRP) for addresses. */
export const DEFAULT_HRP = 'anim';

/** Numeric identifiers for supported algorithms (stable on-chain, 2-byte big-endian). */
export const ALGO_IDS: Record<KeyAlgo, number> = {
  'dilithium3': 0x1001,
  'sphincs_shake_128s': 0x1002,
} as const;

const ID_TO_ALGO: Record<number, KeyAlgo> = Object.fromEntries(
  Object.entries(ALGO_IDS).map(([k, v]) => [v, k as KeyAlgo]),
) as Record<number, KeyAlgo>;

/** Compute SHA3-256(bytes) → Uint8Array(32). */
async function sha3_256(bytes: Uint8Array): Promise<Uint8Array> {
  return sha3Fast.create().update(bytes).digest();
}

/**
 * Encode a bech32m address from pubkey bytes and algorithm.
 *
 * @param pubkey - Raw public key bytes for the account (PQ scheme-specific)
 * @param algo - One of the supported KeyAlgo values
 * @param hrp  - Optional HRP override (defaults to "anim")
 */
export async function encodeAddress(
  pubkey: Uint8Array,
  algo: KeyAlgo,
  hrp: string = DEFAULT_HRP,
): Promise<string> {
  if (!(algo in ALGO_IDS)) throw new Error(`Unsupported algo: ${algo}`);
  if (!(pubkey instanceof Uint8Array) || pubkey.length === 0) {
    throw new Error('pubkey must be non-empty Uint8Array');
  }
  const id = ALGO_IDS[algo];
  const hash = await sha3_256(pubkey); // 32 bytes
  const payload = new Uint8Array(2 + hash.length);
  // Encode alg_id as 2-byte big-endian
  payload[0] = (id >> 8) & 0xff;
  payload[1] = id & 0xff;
  payload.set(hash, 2);

  const words = toWords(payload); // 8-bit → 5-bit
  return encodeBech32m(hrp, words);
}

/**
 * Decode a bech32m address and return { algo, hash, hrp }.
 * The returned `hash` is the 32-byte SHA3-256(pubkey) digest stored in the address.
 */
export function decodeAddress(addr: string): { hrp: string; algo: KeyAlgo; hash: Uint8Array } {
  const { hrp, words } = decodeBech32m(addr);
  const payload = fromWords(words); // 5-bit → 8-bit

  if (payload.length !== 34) {
    throw new Error(`Invalid address payload length: ${payload.length}, expected 34`);
  }
  // Decode 2-byte big-endian alg_id
  const id = (payload[0] << 8) | payload[1];
  const algo = ID_TO_ALGO[id];
  if (!algo) throw new Error(`Unknown algorithm id in address: 0x${id.toString(16).padStart(4, '0')}`);

  const hash = payload.slice(2);
  return { hrp, algo, hash };
}

/** Validate address HRP and (optionally) expected algo. Throws on error, returns true on success. */
export function assertValidAddress(addr: string, expected?: { hrp?: string; algo?: KeyAlgo }): true {
  const { hrp, algo } = decodeAddress(addr);
  if (expected?.hrp && expected.hrp !== hrp) {
    throw new Error(`Address HRP mismatch: expected ${expected.hrp}, got ${hrp}`);
  }
  if (expected?.algo && expected.algo !== algo) {
    throw new Error(`Address algo mismatch: expected ${expected.algo}, got ${algo}`);
  }
  return true;
}

/** Convenience shim used by keyring: derive bech32m address directly from pubkey bytes. */
export async function bech32AddressFromPub(pubkey: Uint8Array, algo: KeyAlgo, hrp: string = DEFAULT_HRP): Promise<string> {
  return encodeAddress(pubkey, algo, hrp);
}

/** Quick boolean validation wrapper (never throws). */
export function isValidAddress(addr: string, expected?: { hrp?: string; algo?: KeyAlgo }): boolean {
  try {
    assertValidAddress(addr, expected);
    return true;
  } catch {
    return false;
  }
}

/** Convenience: shorten anim1... address for UI. */
export function shortAddress(addr: string, left = 6, right = 6): string {
  if (addr.length <= left + right + 3) return addr;
  return `${addr.slice(0, left)}…${addr.slice(-right)}`;
}
