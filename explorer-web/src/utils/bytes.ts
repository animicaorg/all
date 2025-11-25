/**
 * Byte utilities (Browser + Node)
 * -----------------------------------------------------------------------------
 * Zero-dependency helpers for working with hex strings and Uint8Array bytes.
 * Mirrors conventions used across the Animica SDKs:
 *  - Hex strings are lowercase and 0x-prefixed.
 *  - Conversions validate inputs and throw on malformed data.
 */

export type Hex = `0x${string}`;

const HEX_ALPHABET = '0123456789abcdef';

/* --------------------------------- Guards ---------------------------------- */

export function isUint8Array(v: unknown): v is Uint8Array {
  return v instanceof Uint8Array;
}

/** Throw if value is not a Uint8Array. */
export function assertUint8Array(v: unknown, name = 'value'): asserts v is Uint8Array {
  if (!isUint8Array(v)) throw new TypeError(`${name} must be a Uint8Array`);
}

/* ---------------------------------- Hex ------------------------------------ */

export function add0x(hex: string): Hex {
  return (hex.startsWith('0x') ? hex : ('0x' + hex)) as Hex;
}

export function strip0x(hex: string): string {
  return hex.startsWith('0x') ? hex.slice(2) : hex;
}

/** Validate hex string form; enforces 0x prefix if requirePrefix=true (default). */
export function isHexString(
  v: unknown,
  opts: { evenLength?: boolean; requirePrefix?: boolean } = { evenLength: true, requirePrefix: true },
): v is Hex | string {
  if (typeof v !== 'string') return false;
  const { evenLength = true, requirePrefix = true } = opts;
  const s = strip0x(v);
  if (requirePrefix && !String(v).startsWith('0x')) return false;
  if (s.length === 0) return true; // allow "0x"
  if (evenLength && (s.length % 2) !== 0) return false;
  return /^[0-9a-fA-F]+$/.test(s);
}

/** Normalize to lowercase, 0x-prefixed, even-length hex. */
export function toHex(input: Uint8Array | Hex | string): Hex {
  if (isUint8Array(input)) return bytesToHex(input);
  if (!isHexString(input, { requirePrefix: false, evenLength: false })) {
    throw new TypeError('toHex: input must be bytes or hex string');
  }
  let s = strip0x(String(input)).toLowerCase();
  if (s.length % 2) s = '0' + s;
  return add0x(s);
}

/** Convert 0x-hex (or hex) to bytes. Accepts empty "0x". */
export function hexToBytes(hex: Hex | string): Uint8Array {
  if (!isHexString(hex, { requirePrefix: false, evenLength: false })) {
    throw new TypeError('hexToBytes: invalid hex string');
  }
  let s = strip0x(hex);
  if (s.length === 0) return new Uint8Array(0);
  if (s.length % 2) s = '0' + s;
  const out = new Uint8Array(s.length / 2);
  for (let i = 0, j = 0; i < s.length; i += 2, j++) {
    out[j] = (nibble(s[i]) << 4) | nibble(s[i + 1]);
  }
  return out;
}

function nibble(ch: string): number {
  const c = ch.toLowerCase();
  const i = HEX_ALPHABET.indexOf(c);
  if (i === -1) throw new TypeError(`Invalid hex char: ${ch}`);
  return i;
}

/** Convert bytes to 0x-hex (lowercase). */
export function bytesToHex(bytes: Uint8Array): Hex {
  assertUint8Array(bytes, 'bytes');
  const out = new Array<string>(bytes.length);
  for (let i = 0; i < bytes.length; i++) {
    const b = bytes[i]!;
    out[i] = (b < 16 ? '0' : '') + b.toString(16);
  }
  return add0x(out.join(''));
}

/* ------------------------------- Conversions -------------------------------- */

export function utf8ToBytes(s: string): Uint8Array {
  if (typeof s !== 'string') throw new TypeError('utf8ToBytes: input must be string');
  return new TextEncoder().encode(s);
}

export function bytesToUtf8(bytes: Uint8Array): string {
  assertUint8Array(bytes, 'bytes');
  // fatal=false replaces invalid sequences with U+FFFD (safe for diagnostics/UI)
  return new TextDecoder('utf-8', { fatal: false }).decode(bytes);
}

/** Use for browser+node without Buffer requirement. */
export function bytesToBase64(bytes: Uint8Array): string {
  assertUint8Array(bytes, 'bytes');
  if (typeof btoa === 'function') {
    let bin = '';
    for (let i = 0; i < bytes.length; i++) bin += String.fromCharCode(bytes[i]!);
    return btoa(bin);
  }
  // Node fallback
  // eslint-disable-next-line @typescript-eslint/no-var-requires
  return require('buffer').Buffer.from(bytes).toString('base64');
}

export function base64ToBytes(b64: string): Uint8Array {
  if (typeof b64 !== 'string') throw new TypeError('base64ToBytes: input must be string');
  if (typeof atob === 'function') {
    const bin = atob(b64);
    const out = new Uint8Array(bin.length);
    for (let i = 0; i < bin.length; i++) out[i] = bin.charCodeAt(i) & 0xff;
    return out;
  }
  // Node fallback
  // eslint-disable-next-line @typescript-eslint/no-var-requires
  return new (require('buffer').Buffer)(b64, 'base64');
}

/* -------------------------------- Mutations --------------------------------- */

export function concatBytes(...arrays: Uint8Array[]): Uint8Array {
  if (arrays.length === 1) {
    assertUint8Array(arrays[0], 'bytes');
    return arrays[0]!;
  }
  let total = 0;
  for (const a of arrays) {
    assertUint8Array(a, 'bytes');
    total += a.length;
  }
  const out = new Uint8Array(total);
  let offset = 0;
  for (const a of arrays) {
    out.set(a, offset);
    offset += a.length;
  }
  return out;
}

export function equalBytes(a: Uint8Array, b: Uint8Array): boolean {
  assertUint8Array(a, 'a');
  assertUint8Array(b, 'b');
  if (a.length !== b.length) return false;
  // constant-ish time comparison
  let diff = 0;
  for (let i = 0; i < a.length; i++) diff |= a[i]! ^ b[i]!;
  return diff === 0;
}

export function sliceBytes(bytes: Uint8Array, start?: number, end?: number): Uint8Array {
  assertUint8Array(bytes, 'bytes');
  return bytes.subarray(start ?? 0, end ?? bytes.length);
}

export function zeroPadLeft(bytes: Uint8Array, length: number): Uint8Array {
  assertUint8Array(bytes, 'bytes');
  if (bytes.length > length) throw new RangeError('zeroPadLeft: input longer than target length');
  const out = new Uint8Array(length);
  out.set(bytes, length - bytes.length);
  return out;
}

export function zeroPadRight(bytes: Uint8Array, length: number): Uint8Array {
  assertUint8Array(bytes, 'bytes');
  if (bytes.length > length) throw new RangeError('zeroPadRight: input longer than target length');
  const out = new Uint8Array(length);
  out.set(bytes, 0);
  return out;
}

/* ------------------------------ Integers ↔ Bytes ---------------------------- */

export function intToBytesBE(n: bigint | number, size?: number): Uint8Array {
  const bi = toBigInt(n);
  if (bi < 0n) throw new RangeError('intToBytesBE: negative not supported');
  if (bi === 0n) return size ? new Uint8Array(size) : new Uint8Array([]);
  let hex = bi.toString(16);
  if (hex.length % 2) hex = '0' + hex;
  let bytes = hexToBytes(hex);
  if (size) {
    if (bytes.length > size) throw new RangeError('intToBytesBE: value too large for size');
    if (bytes.length < size) bytes = zeroPadLeft(bytes, size);
  }
  return bytes;
}

export function intFromBytesBE(bytes: Uint8Array): bigint {
  assertUint8Array(bytes, 'bytes');
  let hex = '';
  for (let i = 0; i < bytes.length; i++) {
    const b = bytes[i]!;
    hex += (b < 16 ? '0' : '') + b.toString(16);
  }
  if (hex === '') return 0n;
  return BigInt('0x' + hex);
}

export function intToBytesLE(n: bigint | number, size?: number): Uint8Array {
  const be = intToBytesBE(n, size);
  return reverseBytes(be);
}

export function intFromBytesLE(bytes: Uint8Array): bigint {
  return intFromBytesBE(reverseBytes(bytes));
}

export function reverseBytes(bytes: Uint8Array): Uint8Array {
  assertUint8Array(bytes, 'bytes');
  const out = new Uint8Array(bytes.length);
  for (let i = 0; i < bytes.length; i++) out[i] = bytes[bytes.length - 1 - i]!;
  return out;
}

/* --------------------------------- Random ---------------------------------- */

export function randomBytes(length: number): Uint8Array {
  if (!Number.isInteger(length) || length < 0) throw new RangeError('randomBytes: length must be >= 0 integer');
  const out = new Uint8Array(length);
  const g: Crypto | undefined = (globalThis as any).crypto;
  if (g?.getRandomValues) {
    g.getRandomValues(out);
    return out;
  }
  // Node fallback
  // eslint-disable-next-line @typescript-eslint/no-var-requires
  const nodeCrypto = (() => { try { return require('crypto'); } catch { return null; } })();
  if (!nodeCrypto) throw new Error('Secure randomness unavailable: no global crypto and no Node crypto');
  return new Uint8Array(nodeCrypto.randomBytes(length));
}

/* --------------------------------- Casting ---------------------------------- */

export function toBytes(input: Hex | string | Uint8Array): Uint8Array {
  if (isUint8Array(input)) return input;
  if (isHexString(input, { requirePrefix: false, evenLength: false })) return hexToBytes(input);
  if (typeof input === 'string') return utf8ToBytes(input);
  throw new TypeError('toBytes: unsupported input type');
}

export function toBigInt(n: number | bigint): bigint {
  if (typeof n === 'bigint') return n;
  if (!Number.isFinite(n) || !Number.isInteger(n)) throw new TypeError('toBigInt: number must be a finite integer');
  if (n < 0) throw new RangeError('toBigInt: negative not supported');
  return BigInt(n);
}

/* --------------------------------- Formats ---------------------------------- */

export function formatHex(data: Uint8Array | Hex | string, opts: { prefix?: boolean } = { prefix: true }): Hex | string {
  const { prefix = true } = opts;
  const hex = typeof data === 'string' && isHexString(data, { requirePrefix: false, evenLength: false })
    ? toHex(data)
    : bytesToHex(isUint8Array(data) ? data : toBytes(data));
  return prefix ? hex : strip0x(hex);
}

/* ---------------------------------- Tests ----------------------------------- */
/* Example:
const a = hexToBytes('0x01ff');
const b = hexToBytes('0x0203');
console.assert(bytesToHex(concatBytes(a, b)) === '0x01ff0203');
console.assert(equalBytes(hexToBytes('0x00'), intToBytesBE(0n, 1)));
*/
