import { sha3_256 } from '@noble/hashes/sha3';
import { bech32m } from 'bech32';
import { AlgorithmId, getAlgorithmInfo } from './algorithms.js';

/**
 * HRP (Human-Readable Part) for Animica addresses
 */
export const HRP = 'anim';

/**
 * Maximum bech32m address length
 */
const BECH32_MAX_LEN = 90;

/**
 * Payload length (2 bytes alg_id + 32 bytes digest)
 */
const PAYLOAD_LEN = 34;

/**
 * Compute address payload from public key
 * Format: alg_id (2 bytes BE) + SHA3-256(pubkey) (32 bytes)
 */
export function payloadFromPubkey(pubkey: Uint8Array, algId: number): Uint8Array {
  if (algId < 0 || algId > 0xFFFF) {
    throw new Error(`Algorithm ID must fit in 2 bytes: ${algId}`);
  }
  
  // Compute SHA3-256 digest of public key
  const digest = sha3_256(pubkey);
  
  // Build payload: alg_id (2 bytes big-endian) + digest (32 bytes)
  const payload = new Uint8Array(PAYLOAD_LEN);
  payload[0] = (algId >> 8) & 0xff;
  payload[1] = algId & 0xff;
  payload.set(digest, 2);
  
  return payload;
}

/**
 * Derive Animica bech32m address from public key
 * 
 * @param algId Algorithm ID (e.g., AlgorithmId.DILITHIUM3)
 * @param pubkey Public key bytes
 * @param hrp Human-readable part (default: "anim")
 * @returns Bech32m address (e.g., "anim1qq...")
 */
export function addressFromPubkey(
  algId: AlgorithmId | number,
  pubkey: Uint8Array,
  hrp: string = HRP
): string {
  // Validate algorithm
  getAlgorithmInfo(algId);
  
  // Build payload
  const payload = payloadFromPubkey(pubkey, algId);
  
  // Encode as bech32m
  const words = bech32m.toWords(payload);
  const address = bech32m.encode(hrp, words);
  
  if (address.length > BECH32_MAX_LEN) {
    throw new Error('Encoded address exceeds bech32 length limits');
  }
  
  return address;
}

/**
 * Address record after decoding
 */
export interface AddressRecord {
  hrp: string;
  algId: number;
  digest: Uint8Array; // 32-byte SHA3-256 digest
}

/**
 * Decode Animica bech32m address back to components
 * 
 * @param address Bech32m address
 * @param expectHrp Expected HRP (default: "anim")
 * @returns Address components
 */
export function decodeAddress(
  address: string,
  expectHrp?: string
): AddressRecord {
  // Decode bech32m
  let decoded: { prefix: string; words: number[] };
  try {
    decoded = bech32m.decode(address);
  } catch (e) {
    throw new Error(`Bech32m decode failed: ${e}`);
  }
  
  const { prefix: hrp, words } = decoded;
  
  // Check HRP if specified
  if (expectHrp && hrp !== expectHrp) {
    throw new Error(`HRP mismatch: expected ${expectHrp}, got ${hrp}`);
  }
  
  // Convert from 5-bit to 8-bit
  const payload = new Uint8Array(bech32m.fromWords(words));
  
  // Validate payload length
  if (payload.length !== PAYLOAD_LEN) {
    throw new Error(`Invalid payload length: ${payload.length} != ${PAYLOAD_LEN}`);
  }
  
  // Extract components
  const algId = (payload[0] << 8) | payload[1];
  const digest = payload.slice(2);
  
  // Validate algorithm ID
  try {
    getAlgorithmInfo(algId);
  } catch (e) {
    throw new Error(`Unknown algorithm ID: 0x${algId.toString(16)}`);
  }
  
  return { hrp, algId, digest };
}

/**
 * Validate Animica address
 * 
 * @param address Address to validate
 * @param expectHrp Expected HRP (default: "anim")
 * @param allowedAlgIds Allowed algorithm IDs (optional)
 * @returns true if valid, throws error otherwise
 */
export function validateAddress(
  address: string,
  expectHrp?: string,
  allowedAlgIds?: Set<number>
): boolean {
  const record = decodeAddress(address, expectHrp);
  
  if (allowedAlgIds && !allowedAlgIds.has(record.algId)) {
    throw new Error(`Algorithm ID not allowed: 0x${record.algId.toString(16)}`);
  }
  
  return true;
}

/**
 * Shorten address for display (e.g., "anim1qq...abcd")
 * 
 * @param address Full address
 * @param keep Number of characters to keep on each end
 * @returns Shortened address
 */
export function shortAddress(address: string, keep: number = 6): string {
  if (address.length <= 2 * keep + 3) {
    return address;
  }
  return `${address.slice(0, keep)}…${address.slice(-keep)}`;
}
