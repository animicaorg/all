// Bech32m address encoding/decoding for Animica
// 
// CANONICAL FORMAT (matches packages/animica-crypto and pq/py/address.py)
// - No version byte in bech32m encoding
// - Format: bech32m(hrp="anim", toWords(alg_id || sha3_256(pubkey)))

import { sha3Hash } from './pq';
import type { AddressRecord } from '../../types/wallet';
import { decodeAnimAddress, encodeAnimAddress } from '../../lib/address/animicaAddress';

const DEFAULT_HRP = 'anim';
const ADDRESS_PAYLOAD_LENGTH = 34;
// Wallet-account addresses use a PQ signing alg id in 0x1000..0x1fff
// (Dilithium3 = 0x1001, SPHINCS-128s = 0x1002). Contract addresses
// have alg_id = 0x0000 — they're keyless, derived from the deploy tx.
// Both are valid as a tx `to`, so the address validator accepts either.
const ALG_ID_CONTRACT = 0x0000;
const SUPPORTED_ALG_ID_RANGE = { min: 0x1000, max: 0x1fff };

export interface AddressValidationOptions {
  expectedHrp?: string;
}

export function addressFromPubkey(
  pubkey: Uint8Array,
  algId: number,
  options: AddressValidationOptions = {}
): string {
  const hrp = options.expectedHrp ?? DEFAULT_HRP;

  const digest = sha3Hash(pubkey);
  const payload = new Uint8Array(2 + digest.length);
  payload[0] = (algId >> 8) & 0xff;
  payload[1] = algId & 0xff;
  payload.set(digest, 2);

  return encodeAnimAddress(hrp, payload);
}

export function decodeAddress(address: string, options: AddressValidationOptions = {}): AddressRecord {
  const decoded = decodeAnimAddress(address, options);

  if (decoded.payload.length !== ADDRESS_PAYLOAD_LENGTH) {
    throw new Error(`Invalid address payload length: expected ${ADDRESS_PAYLOAD_LENGTH} bytes, got ${decoded.payload.length}`);
  }

  const algId = (decoded.payload[0] << 8) | decoded.payload[1];
  const inWalletRange = algId >= SUPPORTED_ALG_ID_RANGE.min && algId <= SUPPORTED_ALG_ID_RANGE.max;
  if (algId !== ALG_ID_CONTRACT && !inWalletRange) {
    throw new Error(`Unsupported address algorithm id: ${algId}`);
  }

  return {
    hrp: decoded.hrp,
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
