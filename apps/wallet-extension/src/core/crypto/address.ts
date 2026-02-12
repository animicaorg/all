// Bech32m address encoding/decoding for Animica

import { bech32m } from 'bech32';
import { sha3Hash } from './pq';
import type { AddressRecord } from '../../types/wallet';

const DEFAULT_HRP = 'anim';
const DEFAULT_ADDRESS_VERSION = 1;
const ADDRESS_PAYLOAD_LENGTH = 34;
const SUPPORTED_ALG_ID_RANGE = { min: 0x1000, max: 0x1fff };

export interface AddressValidationOptions {
  expectedHrp?: string;
  supportedVersions?: readonly number[];
}

export function addressFromPubkey(
  pubkey: Uint8Array,
  algId: number,
  options: AddressValidationOptions = {}
): string {
  const hrp = options.expectedHrp ?? DEFAULT_HRP;
  const version = options.supportedVersions?.[0] ?? DEFAULT_ADDRESS_VERSION;

  // Address format: bech32m(hrp, version byte, alg_id (2 bytes BE) || SHA3-256(pubkey)).
  // Version is allowlisted because multiple on-chain formats (v1, v2, ...) can coexist.
  const digest = sha3Hash(pubkey);
  const payload = new Uint8Array(2 + digest.length);

  // Encode alg_id as 2-byte big-endian
  payload[0] = (algId >> 8) & 0xff;
  payload[1] = algId & 0xff;

  // Append digest
  payload.set(digest, 2);

  const words = bech32m.toWords(payload);
  return bech32m.encode(hrp, [version, ...words]);
}

export function decodeAddress(address: string, options: AddressValidationOptions = {}): AddressRecord {
  const decoded = bech32m.decode(address);
  const expectedHrp = options.expectedHrp ?? DEFAULT_HRP;
  const supportedVersions = options.supportedVersions ?? [1, 2];

  if (decoded.prefix !== expectedHrp) {
    throw new Error(`Invalid address prefix: expected ${expectedHrp}, got ${decoded.prefix}`);
  }

  if (decoded.words.length < 1) {
    throw new Error('Invalid address: missing version word');
  }

  // In our format, the first 5-bit bech32m word is an internal address format version.
  const version = decoded.words[0];
  if (!supportedVersions.includes(version)) {
    throw new Error(`Unsupported address version ${version} (supported: ${supportedVersions.join(',')})`);
  }

  const payload = new Uint8Array(bech32m.fromWords(decoded.words.slice(1)));

  if (payload.length !== ADDRESS_PAYLOAD_LENGTH) {
    throw new Error(`Invalid address payload length: expected ${ADDRESS_PAYLOAD_LENGTH} bytes, got ${payload.length}`);
  }

  const algId = (payload[0] << 8) | payload[1];
  if (algId < SUPPORTED_ALG_ID_RANGE.min || algId > SUPPORTED_ALG_ID_RANGE.max) {
    throw new Error(`Unsupported address algorithm id: ${algId}`);
  }

  const digest = payload.slice(2, ADDRESS_PAYLOAD_LENGTH);

  return {
    hrp: decoded.prefix,
    version,
    algId,
    digest,
  };
}

export function validateAddress(address: string, options: AddressValidationOptions = {}): boolean {
  try {
    decodeAddress(address, options);
    return true;
  } catch {
    return false;
  }
}

// Convert bech32m address to 32-byte digest for use in transactions
export function addressToBytes(address: string, options: AddressValidationOptions = {}): Uint8Array {
  const decoded = decodeAddress(address, options);
  return decoded.digest;
}
