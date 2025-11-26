/**
 * bytes.ts — small, dependency-free bytes/hex helpers for Studio Web.
 *
 * These utilities are browser-friendly (TextEncoder/TextDecoder) and avoid
 * pulling in heavy dependencies. They mirror the functionality we use across
 * the Studio app: encoding/decoding hex & UTF-8, concatenation, comparison,
 * big-endian integer conversions, and unsigned varint (LEB128) helpers.
 */

export type Hex = `0x${string}`;

/* ---------------------------------------------------------------------------------------------
 * Hex helpers
 * -------------------------------------------------------------------------------------------*/

/** Returns true if the input is a hex string with 0x prefix. If evenLength=true, enforces even nibble count. */
export function isHex(input: unknown, evenLength = false): input is Hex {
  if (typeof input !== 'string') return false;
  if (!input.startsWith('0x')) return false;
  const body = input.slice(2);
  if (!/^[0-9a-fA-F]*$/.test(body)) return false;
  if (evenLength && body.length % 2 !== 0) return false;
  return true;
}

/** Normalizes to lowercase 0x-prefixed hex and ensures even length (left-pads a '0' nibble if needed). */
export function normalizeHex(hex: string): Hex {
  if (!hex.startsWith('0x')) hex = '0x' + hex;
  const body = hex.slice(2);
  if (!/^[0-9a-fA-F]*$/.test(body)) throw new TypeError('Invalid hex characters');
  const evenBody = body.length % 2 === 1 ? '0' + body : body;
  return (`0x${evenBody.toLowerCase()}`) as Hex;
}

/** Converts 0x-hex (odd length allowed) to a Uint8Array. */
export function hexToBytes(hex: Hex | string): Uint8Array {
  const n = normalizeHex(hex);
  const body = n.slice(2);
  const out = new Uint8Array(body.length / 2);
  for (let i = 0; i < out.length; i++) {
    const byte = body.slice(i * 2, i * 2 + 2);
    out[i] = Number.parseInt(byte, 16);
  }
  return out;
}

/** Converts bytes to lowercase 0x-hex string. */
export function bytesToHex(bytes: ArrayLike<number>): Hex {
  const hex = [];
  for (let i = 0; i < bytes.length; i++) {
    const b = bytes[i]! & 0xff;
    hex.push((b >>> 4).toString(16));
    hex.push((b & 0x0f).toString(16));
  }
  return ('0x' + hex.join('')) as Hex;
}

/** Convenience aliases for compatibility with older imports. */
export const bytesFromHex = hexToBytes;
export const hexFromBytes = bytesToHex;

/* ---------------------------------------------------------------------------------------------
 * Bytes utilities
 * -------------------------------------------------------------------------------------------*/

export function concatBytes(...chunks: ArrayLike<number>[]): Uint8Array {
  let total = 0;
  for (const c of chunks) total += c.length;
  const out = new Uint8Array(total);
  let off = 0;
  for (const c of chunks) {
    out.set(c as Uint8Array, off);
    off += c.length;
  }
  return out;
}

/** Constant-time-ish equality (branch-minimized, still JS). */
export function equalBytes(a: ArrayLike<number>, b: ArrayLike<number>): boolean {
  if (a.length !== b.length) return false;
  let diff = 0;
  for (let i = 0; i < a.length; i++) diff |= (a[i]! ^ b[i]!);
  return diff === 0;
}

const _te = new TextEncoder();
const _td = new TextDecoder();

/** UTF-8 string → bytes */
export function utf8ToBytes(s: string): Uint8Array {
  return _te.encode(s);
}

/** bytes → UTF-8 string */
export function bytesToUtf8(bytes: ArrayLike<number>): string {
  return _td.decode(bytes as ArrayBufferView);
}

/** Left-pad a byte array with zeros to desired length (throws if array is longer than len). */
export function leftPad(bytes: ArrayLike<number>, len: number): Uint8Array {
  if (bytes.length > len) throw new RangeError('leftPad: input longer than target length');
  const out = new Uint8Array(len);
  out.set(bytes as Uint8Array, len - bytes.length);
  return out;
}

/** Right-pad a byte array with zeros to desired length (throws if array is longer than len). */
export function rightPad(bytes: ArrayLike<number>, len: number): Uint8Array {
  if (bytes.length > len) throw new RangeError('rightPad: input longer than target length');
  const out = new Uint8Array(len);
  out.set(bytes as Uint8Array, 0);
  return out;
}

/** Returns a copy of bytes[start:end] (Python-like slicing; end is exclusive). */
export function sliceBytes(bytes: ArrayLike<number>, start = 0, end = bytes.length): Uint8Array {
  const a = bytes as Uint8Array;
  return a.subarray ? a.subarray(start, end) : new Uint8Array(Array.prototype.slice.call(a, start, end));
}

/** Accepts hex, string, number, bigint, ArrayBufferView and returns bytes (best-effort). */
export function toBytes(
  v: Hex | string | number | bigint | ArrayBuffer | ArrayBufferView
): Uint8Array {
  if (typeof v === 'string') {
    // Heuristic: treat as hex if starts with 0x, otherwise UTF-8
    return v.startsWith('0x') ? hexToBytes(v) : utf8ToBytes(v);
  }
  if (typeof v === 'number') {
    if (!Number.isFinite(v) || v < 0) throw new RangeError('toBytes: number must be finite and non-negative');
    return numberToBeBytes(BigInt(v));
  }
  if (typeof v === 'bigint') {
    return numberToBeBytes(v);
  }
  if (v instanceof ArrayBuffer) return new Uint8Array(v);
  if (ArrayBuffer.isView(v)) return new Uint8Array(v.buffer, v.byteOffset, v.byteLength);
  throw new TypeError('toBytes: unsupported type');
}

/* ---------------------------------------------------------------------------------------------
 * Big-endian integer conversions
 * -------------------------------------------------------------------------------------------*/

/** number/bigint → big-endian bytes (minimal length unless len specified; 0→empty unless len>0). */
export function numberToBeBytes(n: number | bigint, len?: number): Uint8Array {
  let x = typeof n === 'number' ? BigInt(n) : n;
  if (x < 0n) throw new RangeError('numberToBeBytes: negative not supported');
  // Special-case zero
  if (x === 0n) {
    return len ? new Uint8Array(len) : new Uint8Array(0);
  }
  const tmp: number[] = [];
  while (x > 0) {
    tmp.push(Number(x & 0xffn));
    x >>= 8n;
  }
  tmp.reverse();
  const out = new Uint8Array(tmp);
  return typeof len === 'number' ? leftPad(out, len) : out;
}

/** big-endian bytes → bigint (treats empty as 0n). */
export function beBytesToBigint(bytes: ArrayLike<number>): bigint {
  let x = 0n;
  for (let i = 0; i < bytes.length; i++) {
    x = (x << 8n) | BigInt(bytes[i]! & 0xff);
  }
  return x;
}

/* ---------------------------------------------------------------------------------------------
 * Unsigned varint (LEB128) helpers
 * -------------------------------------------------------------------------------------------*/

/** Encodes an unsigned integer into LEB128 (uvarint). */
export function encodeUvarint(n: number | bigint): Uint8Array {
  let x = typeof n === 'number' ? BigInt(n) : n;
  if (x < 0n) throw new RangeError('encodeUvarint: negative not allowed');
  const out: number[] = [];
  do {
    let byte = Number(x & 0x7fn);
    x >>= 7n;
    if (x !== 0n) byte |= 0x80;
    out.push(byte);
  } while (x !== 0n);
  return new Uint8Array(out);
}

/**
 * Decodes an unsigned LEB128 varint from bytes[offset:].
 * Returns the value and number of bytes read.
 */
export function decodeUvarint(
  bytes: ArrayLike<number>,
  offset = 0
): { value: bigint; bytesRead: number } {
  let x = 0n;
  let s = 0n;
  let i = offset;
  for (; i < bytes.length; i++) {
    const b = BigInt(bytes[i]! & 0xff);
    x |= (b & 0x7fn) << s;
    if ((b & 0x80n) === 0n) {
      return { value: x, bytesRead: i - offset + 1 };
    }
    s += 7n;
    if (s > 63n) throw new RangeError('decodeUvarint: varint too large');
  }
  throw new RangeError('decodeUvarint: truncated input');
}

/* ---------------------------------------------------------------------------------------------
 * Misc
 * -------------------------------------------------------------------------------------------*/

/** Returns a zero-filled Uint8Array of length n. */
export function zeroBytes(n: number): Uint8Array {
  if (!Number.isInteger(n) || n < 0) throw new RangeError('zeroBytes: length must be a non-negative integer');
  return new Uint8Array(n);
}
