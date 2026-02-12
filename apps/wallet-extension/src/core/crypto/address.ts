// Bech32m address encoding/decoding for Animica

import { sha3Hash } from './pq';
import type { AddressRecord } from '../../types/wallet';
import { decodeAnimAddress, encodeAnimAddress } from '../../lib/address/animicaAddress';

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

  const digest = sha3Hash(pubkey);
  const payload = new Uint8Array(2 + digest.length);
  payload[0] = (algId >> 8) & 0xff;
  payload[1] = algId & 0xff;
  payload.set(digest, 2);

  return encodeAnimAddress(hrp, version, payload);
}

export function decodeAddress(address: string, options: AddressValidationOptions = {}): AddressRecord {
  const decoded = decodeAnimAddress(address, options);

  if (decoded.payload.length !== ADDRESS_PAYLOAD_LENGTH) {
    throw new Error(`Invalid address payload length: expected ${ADDRESS_PAYLOAD_LENGTH} bytes, got ${decoded.payload.length}`);
  }

  const algId = (decoded.payload[0] << 8) | decoded.payload[1];
  if (algId < SUPPORTED_ALG_ID_RANGE.min || algId > SUPPORTED_ALG_ID_RANGE.max) {
    throw new Error(`Unsupported address algorithm id: ${algId}`);
  }

  return {
    hrp: decoded.hrp,
    version: decoded.version,
    algId,
    digest: decoded.payload.slice(2, ADDRESS_PAYLOAD_LENGTH),
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

export function addressToBytes(address: string, options: AddressValidationOptions = {}): Uint8Array {
  const decoded = decodeAddress(address, options);
  return decoded.digest;
}
