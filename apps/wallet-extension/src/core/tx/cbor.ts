// Canonical CBOR encoding for Animica transactions.
// This mirrors core/encoding/cbor.py (deterministic key ordering by encoded key bytes).

import { sha3Hash } from '../crypto/pq';
import type { SignedTx, UnsignedTx } from '../../types/tx';

function asBytes(value: Uint8Array | number[]): Uint8Array {
  return value instanceof Uint8Array ? value : Uint8Array.from(value);
}

function encodeAi(major: number, n: bigint): number[] {
  if (n < 24n) return [(major << 5) | Number(n)];
  if (n <= 0xffn) return [(major << 5) | 24, Number(n)];
  if (n <= 0xffffn) return [(major << 5) | 25, Number((n >> 8n) & 0xffn), Number(n & 0xffn)];
  if (n <= 0xffffffffn) {
    return [
      (major << 5) | 26,
      Number((n >> 24n) & 0xffn), Number((n >> 16n) & 0xffn), Number((n >> 8n) & 0xffn), Number(n & 0xffn),
    ];
  }
  if (n <= 0xffffffffffffffffn) {
    return [
      (major << 5) | 27,
      Number((n >> 56n) & 0xffn), Number((n >> 48n) & 0xffn), Number((n >> 40n) & 0xffn), Number((n >> 32n) & 0xffn),
      Number((n >> 24n) & 0xffn), Number((n >> 16n) & 0xffn), Number((n >> 8n) & 0xffn), Number(n & 0xffn),
    ];
  }
  throw new Error('Integer too large for canonical CBOR in wallet-extension');
}

function encodeInt(n: bigint): number[] {
  if (n >= 0n) return encodeAi(0, n);
  return encodeAi(1, -1n - n);
}

function encodeBytes(data: Uint8Array): number[] {
  return [...encodeAi(2, BigInt(data.length)), ...data];
}

function encodeText(s: string): number[] {
  const data = new TextEncoder().encode(s);
  return [...encodeAi(3, BigInt(data.length)), ...data];
}

function encodeArray(items: unknown[]): number[] {
  const out = [...encodeAi(4, BigInt(items.length))];
  for (const item of items) out.push(...encodeAny(item));
  return out;
}

function compareBytes(a: Uint8Array, b: Uint8Array): number {
  const n = Math.min(a.length, b.length);
  for (let i = 0; i < n; i++) {
    const d = a[i] - b[i];
    if (d !== 0) return d;
  }
  return a.length - b.length;
}

function encodeMap(obj: Record<string, unknown>): number[] {
  const pairs: { k: Uint8Array; v: Uint8Array }[] = [];
  for (const [k, v] of Object.entries(obj)) {
    const kb = asBytes(encodeAny(k));
    const vb = asBytes(encodeAny(v));
    pairs.push({ k: kb, v: vb });
  }
  pairs.sort((a, b) => compareBytes(a.k, b.k));

  const out = [...encodeAi(5, BigInt(pairs.length))];
  for (const p of pairs) {
    out.push(...p.k, ...p.v);
  }
  return out;
}

function encodeAny(value: unknown): number[] {
  if (value === null || value === undefined) return [0xf6];
  if (value === false) return [0xf4];
  if (value === true) return [0xf5];

  if (typeof value === 'number') {
    if (!Number.isInteger(value)) throw new Error('Only integers are supported in canonical tx CBOR');
    return encodeInt(BigInt(value));
  }

  if (typeof value === 'bigint') return encodeInt(value);
  if (typeof value === 'string') return encodeText(value);
  if (value instanceof Uint8Array) return encodeBytes(value);
  if (Array.isArray(value)) return encodeArray(value);
  if (typeof value === 'object') return encodeMap(value as Record<string, unknown>);

  throw new Error(`Unsupported CBOR type: ${typeof value}`);
}

// Canonical CBOR encoding matching node codec for tx envelopes.
export function encodeCanonical(obj: any): Uint8Array {
  return asBytes(encodeAny(obj));
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
