/**
 * Bytes utilities used across the wallet:
 *  - Hex ⇄ Uint8Array (prefix 0x optional on input, preferred on output)
 *  - UTF-8 ⇄ Uint8Array
 *  - concat/equal/slice helpers
 *  - type guards and normalizers
 *  - crypto-safe random bytes (uses Web Crypto)
 */

export type BytesLike = Uint8Array | ArrayBuffer | number[];

/* --------------------------------- Hex codec -------------------------------- */

export function toHex(bytes: Uint8Array, withPrefix = true): string {
  const hex: string[] = new Array(bytes.length);
  for (let i = 0; i < bytes.length; i++) {
    hex[i] = bytes[i].toString(16).padStart(2, '0');
  }
  return (withPrefix ? '0x' : '') + hex.join('');
}

export function fromHex(hex: string): Uint8Array {
  let h = hex.trim().toLowerCase();
  if (h.startsWith('0x')) h = h.slice(2);
  if (h.length % 2 !== 0) h = '0' + h;
  const out = new Uint8Array(h.length / 2);
  for (let i = 0; i < out.length; i++) {
    const byte = Number.parseInt(h.slice(i * 2, i * 2 + 2), 16);
    if (Number.isNaN(byte)) throw new Error('Invalid hex string');
    out[i] = byte;
  }
  return out;
}

/* ------------------------------- UTF-8 codec -------------------------------- */

const _te = new TextEncoder();
const _td = new TextDecoder();

export function utf8ToBytes(s: string): Uint8Array {
  return _te.encode(s);
}

export function bytesToUtf8(b: Uint8Array): string {
  return _td.decode(b);
}

/* ------------------------------- Conversions -------------------------------- */

export function isUint8Array(x: unknown): x is Uint8Array {
  return x instanceof Uint8Array;
}

export function asU8(a: BytesLike): Uint8Array {
  if (a instanceof Uint8Array) return a;
  if (a instanceof ArrayBuffer) return new Uint8Array(a);
  if (Array.isArray(a)) return new Uint8Array(a);
  throw new Error('Unsupported BytesLike');
}

/** Normalize unknown input to Uint8Array.
 *  - Uint8Array/ArrayBuffer/number[] are passed through/asU8
 *  - string is treated as hex if it starts with 0x, otherwise as UTF-8 text
 */
export function toBytes(x: Uint8Array | ArrayBuffer | number[] | string): Uint8Array {
  if (typeof x === 'string') {
    return x.startsWith('0x') ? fromHex(x) : utf8ToBytes(x);
  }
  return asU8(x);
}

/* --------------------------------- Helpers ---------------------------------- */

export function concatBytes(...parts: Uint8Array[]): Uint8Array {
  const total = parts.reduce((n, p) => n + p.length, 0);
  const out = new Uint8Array(total);
  let off = 0;
  for (const p of parts) {
    out.set(p, off);
    off += p.length;
  }
  return out;
}

export function equalBytes(a: Uint8Array, b: Uint8Array): boolean {
  if (a === b) return true;
  if (a.length !== b.length) return false;
  // Constant-ish time compare
  let diff = 0;
  for (let i = 0; i < a.length; i++) diff |= a[i] ^ b[i];
  return diff === 0;
}

/** Lexicographic unsigned compare: returns -1, 0, 1. */
export function compareBytes(a: Uint8Array, b: Uint8Array): number {
  const len = Math.min(a.length, b.length);
  for (let i = 0; i < len; i++) {
    if (a[i] !== b[i]) return a[i] < b[i] ? -1 : 1;
  }
  return a.length === b.length ? 0 : a.length < b.length ? -1 : 1;
}

export function sliceBytes(b: Uint8Array, start = 0, end = b.length): Uint8Array {
  return b.subarray(start, end);
}

export function padStartBytes(b: Uint8Array, length: number, pad = 0x00): Uint8Array {
  if (b.length >= length) return b;
  const out = new Uint8Array(length);
  out.fill(pad, 0, length - b.length);
  out.set(b, length - b.length);
  return out;
}

export function padEndBytes(b: Uint8Array, length: number, pad = 0x00): Uint8Array {
  if (b.length >= length) return b;
  const out = new Uint8Array(length);
  out.set(b, 0);
  out.fill(pad, b.length);
  return out;
}

/* ------------------------------- Random bytes ------------------------------- */

export function randomBytes(length: number): Uint8Array {
  if (length < 0 || !Number.isInteger(length)) throw new Error('length must be a non-negative integer');
  const out = new Uint8Array(length);
  // Use Web Crypto if available
  if (typeof globalThis !== 'undefined' && (globalThis.crypto as Crypto)?.getRandomValues) {
    (globalThis.crypto as Crypto).getRandomValues(out);
    return out;
  }
  // Fallback (non-crypto): not recommended, but keeps dev/test running
  for (let i = 0; i < length; i++) out[i] = Math.floor(Math.random() * 256);
  return out;
}

/* --------------------------------- Numbers ---------------------------------- */

/** Encode a safe unsigned integer to big-endian bytes of minimal length. */
export function uintToBytesBE(n: number | bigint): Uint8Array {
  const big = typeof n === 'bigint' ? n : BigInt(n >>> 0);
  if (big < 0n) throw new Error('unsigned only');
  if (big === 0n) return new Uint8Array([0]);
  let tmp = big;
  const stack: number[] = [];
  while (tmp > 0n) {
    stack.push(Number(tmp & 0xffn));
    tmp >>= 8n;
  }
  stack.reverse();
  return new Uint8Array(stack);
}

/** Parse unsigned big-endian bytes to bigint. */
export function bytesToUintBE(b: Uint8Array): bigint {
  let x = 0n;
  for (let i = 0; i < b.length; i++) x = (x << 8n) | BigInt(b[i]);
  return x;
}

/* ---------------------------------- Re-exports ------------------------------ */
/** Re-export commonly used names for convenience parity with other modules. */
export const hexFromBytes = toHex;
export const bytesFromHex = fromHex;

export default {
  toHex,
  fromHex,
  utf8ToBytes,
  bytesToUtf8,
  isUint8Array,
  asU8,
  toBytes,
  concatBytes,
  equalBytes,
  compareBytes,
  sliceBytes,
  padStartBytes,
  padEndBytes,
  randomBytes,
  uintToBytesBE,
  bytesToUintBE,
};
