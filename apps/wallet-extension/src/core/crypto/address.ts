// Bech32m address encoding/decoding for Animica

import { bech32m } from 'bech32';
import { sha3Hash } from './pq';
import type { AddressRecord } from '../../types/wallet';

const HRP = 'anim';
const VERSION = 1;

export function addressFromPubkey(
  pubkey: Uint8Array,
  algId: number
): string {
  // Address format: bech32m(anim, version=1, alg_id (2 bytes BE) || SHA3-256(pubkey))
  const digest = sha3Hash(pubkey);
  const payload = new Uint8Array(2 + digest.length);
  
  // Encode alg_id as 2-byte big-endian
  payload[0] = (algId >> 8) & 0xff;
  payload[1] = algId & 0xff;
  
  // Append digest
  payload.set(digest, 2);
  
  // Convert to 5-bit words for bech32m
  const words = bech32m.toWords(payload);
  return bech32m.encode(HRP, [VERSION, ...words]);
}

export function decodeAddress(address: string): AddressRecord {
  const decoded = bech32m.decode(address);
  
  if (decoded.prefix !== HRP) {
    throw new Error(`Invalid address prefix: expected ${HRP}, got ${decoded.prefix}`);
  }
  
  if (decoded.words.length < 1) {
    throw new Error('Invalid address: no version byte');
  }
  
  const version = decoded.words[0];
  if (version !== VERSION) {
    throw new Error(`Invalid address version: expected ${VERSION}, got ${version}`);
  }
  
  // Convert words back to bytes
  const payload = new Uint8Array(bech32m.fromWords(decoded.words.slice(1)));
  
  if (payload.length < 34) {
    throw new Error(`Invalid address payload: expected 34 bytes, got ${payload.length}`);
  }
  
  // Extract alg_id (2 bytes big-endian)
  const algId = (payload[0] << 8) | payload[1];
  
  // Extract digest (32 bytes)
  const digest = payload.slice(2, 34);
  
  return {
    hrp: HRP,
    version,
    algId,
    digest,
  };
}

export function validateAddress(address: string): boolean {
  try {
    decodeAddress(address);
    return true;
  } catch {
    return false;
  }
}

// Convert bech32m address to 32-byte digest for use in transactions
export function addressToBytes(address: string): Uint8Array {
  const decoded = decodeAddress(address);
  return decoded.digest;
}
