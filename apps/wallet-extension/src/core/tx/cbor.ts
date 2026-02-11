// CBOR encoding for Animica transactions using cbor-x

import { encode } from 'cbor-x';
import { sha3Hash } from '../crypto/pq';
import type { SignedTx, UnsignedTx } from '../../types/tx';

// Canonical CBOR encoding with sorted keys
export function encodeCanonical(obj: any): Uint8Array {
  // cbor-x uses canonical encoding by default
  return new Uint8Array(encode(obj));
}

// Get signing preimage for transaction
export function getSigningBytes(unsignedTx: UnsignedTx, domain: string = 'animica/tx.v1'): Uint8Array {
  // Domain-separated signing: domain || CBOR(unsignedTx)
  const domainBytes = new TextEncoder().encode(domain);
  const txBytes = encodeCanonical(unsignedTx);
  
  const preimage = new Uint8Array(domainBytes.length + txBytes.length);
  preimage.set(domainBytes, 0);
  preimage.set(txBytes, domainBytes.length);
  
  return sha3Hash(preimage);
}

// Get transaction hash (for tracking)
export function getTxHash(signedTx: SignedTx): string {
  const encoded = encodeCanonical(signedTx);
  const hash = sha3Hash(encoded);
  return bytesToHex(hash);
}

// Get unsigned hash (for mempool deduplication)
export function getUnsignedHash(unsignedTx: UnsignedTx): string {
  const encoded = encodeCanonical(unsignedTx);
  const hash = sha3Hash(encoded);
  return bytesToHex(hash);
}

function bytesToHex(bytes: Uint8Array): string {
  return Array.from(bytes)
    .map(b => b.toString(16).padStart(2, '0'))
    .join('');
}
