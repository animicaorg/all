import { bech32m } from 'bech32';

/**
 * CANONICAL ADDRESS FORMAT (NO VERSION BYTE)
 * 
 * This implementation matches the canonical format used by:
 * - packages/animica-crypto/src/address.ts (TypeScript)
 * - pq/py/address.py (Python node/CLI)
 * - sdk/typescript/src/address.ts (SDK)
 * 
 * Format: bech32m(hrp="anim", toWords(alg_id || sha3_256(pubkey)))
 * 
 * IMPORTANT: This does NOT use a version byte in the bech32m encoding.
 * The previous version-based encoding was non-standard and caused address
 * mismatches between extension and node/CLI.
 */

const DEFAULT_HRP = 'anim';
/**
 * Payload length: 2 bytes alg_id + 32 bytes SHA3-256 digest
 * 
 * IMPORTANT: This is the canonical payload format WITHOUT any version byte.
 * The previous implementation incorrectly added a version byte, causing
 * address mismatches with the node/CLI.
 */
const PAYLOAD_LENGTH = 34; // 2 bytes alg_id + 32 bytes digest

export interface DecodeAnimAddressOptions {
  expectedHrp?: string;
}

export interface DecodedAnimAddress {
  hrp: string;
  payload: Uint8Array;
  bytes: Uint8Array;
}

/**
 * Decode Animica bech32m address (canonical format, no version byte)
 */
export function decodeAnimAddress(address: string, options: DecodeAnimAddressOptions = {}): DecodedAnimAddress {
  const decoded = bech32m.decode(address);
  const expectedHrp = options.expectedHrp ?? DEFAULT_HRP;

  if (decoded.prefix !== expectedHrp) {
    throw new Error(`Invalid address prefix: expected ${expectedHrp}, got ${decoded.prefix}`);
  }

  const payload = new Uint8Array(bech32m.fromWords(decoded.words));
  
  if (payload.length !== PAYLOAD_LENGTH) {
    throw new Error(`Invalid payload length: expected ${PAYLOAD_LENGTH} bytes, got ${payload.length}`);
  }

  return {
    hrp: decoded.prefix,
    payload,
    bytes: payload,
  };
}

/**
 * Encode Animica bech32m address (canonical format, no version byte)
 */
export function encodeAnimAddress(
  hrp: string,
  payload: Uint8Array,
): string {
  if (payload.length !== PAYLOAD_LENGTH) {
    throw new Error(`Invalid payload length: expected ${PAYLOAD_LENGTH} bytes, got ${payload.length}`);
  }

  return bech32m.encode(hrp, bech32m.toWords(payload));
}
