/**
 * Addressing (bech32m, anim1...)
 *
 * Format (payload bytes, then converted to 5-bit words and encoded with bech32m):
 *   payload = alg_id (1 byte) || sha3_256(pubkey_bytes) (32 bytes)
 *   address = bech32m_encode(hrp="anim", payload)
 *
 * Notes
 * - We use SHA3-256 over the raw PQ public key bytes.
 * - alg_id distinguishes the signing scheme used by the account.
 * - HRP can be overridden by network config (e.g., "animt" for testnets).
 *
 * This module intentionally avoids storing any secret material.
 */

import type { KeyAlgo } from './storage';
import { toWords, fromWords, bech32mEncode, bech32mDecode } from '../../utils/bech32';

/** Default human-readable part (HRP) for addresses. */
export const DEFAULT_HRP = 'anim';

/** Numeric identifiers for supported algorithms (stable on-chain). */
export const ALGO_IDS: Record<KeyAlgo, number> = {
  'dilithium3': 0x01,
  'sphincs-shake-128s': 0x02,
} as const;

const ID_TO_ALGO: Record<number, KeyAlgo> = Object.fromEntries(
  Object.entries(ALGO_IDS).map(([k, v]) => [v, k as KeyAlgo]),
) as Record<number, KeyAlgo>;

/** Compute SHA3-256(bytes) → Uint8Array(32). */
async function sha3_256(bytes: Uint8Array): Promise<Uint8Array> {
  // Prefer @noble/hashes (ESM, tiny & fast). Fallback to js-sha3 if available.
  try {
    const mod = await import(/* @vite-ignore */ '@noble/hashes/sha3');
    const out = mod.sha3_256.create().update(bytes).digest();
    return new Uint8Array(out);
  } catch {
    try {
      const mod = await import(/* @vite-ignore */ 'js-sha3');
      // js-sha3 returns hex by default; request ArrayBuffer if available
      if (typeof (mod as any).sha3_256 === 'function') {
        const h = (mod as any).sha3_256;
        // Prefer arrayBuffer() if present; else hex → bytes
        if (h.arrayBuffer) {
          const ab: ArrayBuffer = h.arrayBuffer(bytes);
          return new Uint8Array(ab);
        }
        const hex: string = h(bytes);
        return hexToBytes(hex);
      }
    } catch {
      // no-op; fall through to explicit error
    }
    throw new Error(
      'SHA3-256 implementation not found. Install @noble/hashes or js-sha3 in wallet-extension/package.json.',
    );
  }
}

function hexToBytes(hex: string): Uint8Array {
  const s = hex.startsWith('0x') ? hex.slice(2) : hex;
  if (s.length % 2 !== 0) throw new Error('Invalid hex length');
  const out = new Uint8Array(s.length / 2);
  for (let i = 0; i < out.length; i++) {
    out[i] = parseInt(s.slice(i * 2, i * 2 + 2), 16);
  }
  return out;
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
  const id = ALGO_IDS[algo] & 0xff;
  const hash = await sha3_256(pubkey); // 32 bytes
  const payload = new Uint8Array(1 + hash.length);
  payload[0] = id;
  payload.set(hash, 1);

  const words = toWords(payload); // 8-bit → 5-bit
  return bech32mEncode(hrp, words);
}

/**
 * Decode a bech32m address and return { algo, hash, hrp }.
 * The returned `hash` is the 32-byte SHA3-256(pubkey) digest stored in the address.
 */
export function decodeAddress(addr: string): { hrp: string; algo: KeyAlgo; hash: Uint8Array } {
  const { hrp, words } = bech32mDecode(addr);
  const payload = fromWords(words); // 5-bit → 8-bit

  if (payload.length !== 33) {
    throw new Error(`Invalid address payload length: ${payload.length}, expected 33`);
  }
  const id = payload[0];
  const algo = ID_TO_ALGO[id];
  if (!algo) throw new Error(`Unknown algorithm id in address: 0x${id.toString(16)}`);

  const hash = payload.slice(1);
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
