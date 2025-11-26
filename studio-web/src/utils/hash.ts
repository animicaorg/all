/**
 * hash.ts — thin wrappers for hashing used in Studio Web.
 *
 * We delegate to the well-tested implementations shipped in @animica/sdk,
 * so Studio Web stays lean and consistent with the node/wallet/SDK stack.
 */

import { utf8ToBytes, bytesToHex } from './bytes';
import {
  keccak256 as sdkKeccak256,
  sha3_256 as sdkSha3_256,
  sha3_512 as sdkSha3_512,
} from '../sdk-shim/utils/hash';

export type BytesLike = string | Uint8Array | ArrayBuffer | ArrayBufferView;

function toBytes(input: BytesLike): Uint8Array {
  if (typeof input === 'string') return utf8ToBytes(input);
  if (input instanceof Uint8Array) return input;
  if (input instanceof ArrayBuffer) return new Uint8Array(input);
  if (ArrayBuffer.isView(input)) return new Uint8Array(input.buffer, input.byteOffset, input.byteLength);
  throw new TypeError('toBytes: unsupported input');
}

/** keccak256(input) → Uint8Array */
export function keccak256(input: BytesLike): Uint8Array {
  return sdkKeccak256(toBytes(input));
}

/** sha3_256(input) → Uint8Array */
export function sha3_256(input: BytesLike): Uint8Array {
  return sdkSha3_256(toBytes(input));
}

/** sha3_512(input) → Uint8Array */
export function sha3_512(input: BytesLike): Uint8Array {
  return sdkSha3_512(toBytes(input));
}

/** Hex helpers (convenience) */
export function keccak256Hex(input: BytesLike): string {
  return '0x' + bytesToHex(keccak256(input));
}
export function sha3_256Hex(input: BytesLike): string {
  return '0x' + bytesToHex(sha3_256(input));
}
export function sha3_512Hex(input: BytesLike): string {
  return '0x' + bytesToHex(sha3_512(input));
}

export const sha3_512_hex = sha3_512Hex;

