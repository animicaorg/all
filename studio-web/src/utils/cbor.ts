/**
 * cbor.ts — tiny deterministic CBOR encoder/decoder for Studio Web (browser-safe).
 *
 * Goals:
 *  - Deterministic encoding (canonical map key ordering per RFC 8949 §4.2.1).
 *  - Zero dependencies; uses TextEncoder/TextDecoder/DataView only.
 *  - Supports: integers (number/bigint), bytes (Uint8Array/ArrayBuffer), strings,
 *    arrays, maps/objects, booleans, null, undefined, and float64.
 *  - Decoder returns bigint for integers > Number.MAX_SAFE_INTEGER by default.
 *
 * NOTE: We avoid exotic CBOR features (bignum tags, indefinite-length, etc.) on purpose.
 */

import {
  concatBytes,
  bytesToUtf8,
  utf8ToBytes,
  numberToBeBytes,
} from './bytes';

export type CborBytes = Uint8Array;

export interface DecodeOptions {
  /** If true, integers that don't fit into a JS safe number decode as decimal strings instead of bigint. */
  bigintAsString?: boolean;
}

/* ------------------------------------------------------------------------------------------------
 * Encoding helpers
 * --------------------------------------------------------------------------------------------- */

const enum Major {
  Unsigned = 0,
  Negative = 1,
  Bytes = 2,
  Text = 3,
  Array = 4,
  Map = 5,
  Tag = 6,
  SimpleFloat = 7,
}

/** Encodes the initial type+length header for a major type. len may be number or bigint. */
function encTypeLen(major: Major, len: number | bigint): Uint8Array {
  // Normalize to bigint for unified logic
  const L = typeof len === 'number' ? BigInt(len) : len;
  if (L < 0n) throw new RangeError('encTypeLen: negative length');
  if (L <= 23n) {
    return new Uint8Array([ (major << 5) | Number(L) ]);
  } else if (L <= 0xffn) {
    return new Uint8Array([ (major << 5) | 24, Number(L) ]);
  } else if (L <= 0xffffn) {
    const b = new Uint8Array(3);
    b[0] = (major << 5) | 25;
    b[1] = Number((L >> 8n) & 0xffn);
    b[2] = Number(L & 0xffn);
    return b;
  } else if (L <= 0xffffffffn) {
    const b = new Uint8Array(5);
    b[0] = (major << 5) | 26;
    b[1] = Number((L >> 24n) & 0xffn);
    b[2] = Number((L >> 16n) & 0xffn);
    b[3] = Number((L >> 8n) & 0xffn);
    b[4] = Number(L & 0xffn);
    return b;
  } else {
    const b = new Uint8Array(9);
    b[0] = (major << 5) | 27;
    // 64-bit big-endian
    for (let i = 0; i < 8; i++) {
      b[8 - i] = Number((L >> (BigInt(i) * 8n)) & 0xffn);
    }
    return b;
  }
}

function encUnsigned(n: number | bigint): Uint8Array {
  const x = typeof n === 'number' ? BigInt(n) : n;
  if (x < 0n) throw new RangeError('encUnsigned: negative');
  // length is encoded as the integer itself
  // For 64-bit case, header selects 27 and includes 8-byte integer.
  return encTypeLen(Major.Unsigned, x);
}

function encNegative(n: number | bigint): Uint8Array {
  const x = typeof n === 'number' ? BigInt(n) : n;
  if (x >= 0n) throw new RangeError('encNegative: non-negative');
  // For negative integers, value is (-1 - n)
  const val = -1n - x;
  return encTypeLen(Major.Negative, val);
}

function encBytes(bytes: Uint8Array): Uint8Array {
  return concatBytes(encTypeLen(Major.Bytes, bytes.length), bytes);
}

function encText(s: string): Uint8Array {
  const b = utf8ToBytes(s);
  return concatBytes(encTypeLen(Major.Text, b.length), b);
}

function encArray(items: any[]): Uint8Array {
  const parts: Uint8Array[] = [encTypeLen(Major.Array, items.length)];
  for (const it of items) parts.push(encode(it));
  return concatBytes(...parts);
}

function compareLex(a: Uint8Array, b: Uint8Array): number {
  const n = Math.min(a.length, b.length);
  for (let i = 0; i < n; i++) {
    const da = a[i]!, db = b[i]!;
    if (da !== db) return da - db;
  }
  return a.length - b.length;
}

/**
 * Canonical map encoding:
 *  - Keys are encoded individually to CBOR bytes to derive the canonical order
 *    (bytewise lexicographic).
 *  - We then emit the map header and key/value pairs in that order.
 */
function encMapEntries(entries: Array<[any, any]>): Uint8Array {
  const tmp: Array<{ kEnc: Uint8Array; k: any; v: any }> = [];
  for (const [k, v] of entries) {
    const kEnc = encode(k);
    tmp.push({ kEnc, k, v });
  }
  tmp.sort((a, b) => compareLex(a.kEnc, b.kEnc));
  const parts: Uint8Array[] = [encTypeLen(Major.Map, tmp.length)];
  for (const { kEnc, v } of tmp) {
    parts.push(kEnc, encode(v));
  }
  return concatBytes(...parts);
}

function isPojo(v: any): v is Record<string, any> {
  if (v === null || typeof v !== 'object') return false;
  const proto = Object.getPrototypeOf(v);
  return proto === Object.prototype || proto === null;
}

/**
 * Encodes a JS value to deterministic CBOR.
 */
export function encode(value: any): Uint8Array {
  // Integers
  if (typeof value === 'number') {
    if (!Number.isFinite(value)) throw new RangeError('encode: non-finite number not supported');
    if (Number.isInteger(value)) {
      return value >= 0 ? encUnsigned(value) : encNegative(value);
    }
    // float64
    const out = new Uint8Array(9);
    out[0] = (Major.SimpleFloat << 5) | 27; // 64-bit float
    const dv = new DataView(out.buffer);
    dv.setFloat64(1, value, false); // big-endian
    return out;
  }

  if (typeof value === 'bigint') {
    return value >= 0n ? encUnsigned(value) : encNegative(value);
  }

  // Bytes
  if (value instanceof Uint8Array) return encBytes(value);
  if (value instanceof ArrayBuffer) return encBytes(new Uint8Array(value));
  if (ArrayBuffer.isView(value)) return encBytes(new Uint8Array(value.buffer, value.byteOffset, value.byteLength));

  // String
  if (typeof value === 'string') return encText(value);

  // Booleans / null / undefined
  if (value === false) return new Uint8Array([ (Major.SimpleFloat << 5) | 20 ]);
  if (value === true)  return new Uint8Array([ (Major.SimpleFloat << 5) | 21 ]);
  if (value === null)  return new Uint8Array([ (Major.SimpleFloat << 5) | 22 ]);
  if (value === undefined) return new Uint8Array([ (Major.SimpleFloat << 5) | 23 ]);

  // Arrays
  if (Array.isArray(value)) return encArray(value);

  // Map
  if (value instanceof Map) {
    return encMapEntries(Array.from(value.entries()));
  }

  // Plain object → map of entries
  if (isPojo(value)) {
    return encMapEntries(Object.entries(value));
  }

  throw new TypeError(`encode: unsupported type: ${Object.prototype.toString.call(value)}`);
}

/* ------------------------------------------------------------------------------------------------
 * Decoding helpers
 * --------------------------------------------------------------------------------------------- */

class Cursor {
  constructor(public buf: Uint8Array, public i: number = 0) {}
  eof(): boolean { return this.i >= this.buf.length; }
  u8(): number {
    if (this.i >= this.buf.length) throw new RangeError('decode: unexpected end of input');
    return this.buf[this.i++]!;
  }
  take(n: number): Uint8Array {
    if (this.i + n > this.buf.length) throw new RangeError('decode: truncated');
    const out = this.buf.subarray(this.i, this.i + n);
    this.i += n;
    return out;
  }
}

function readLen(ai: number, cur: Cursor): bigint {
  if (ai < 24) return BigInt(ai);
  if (ai === 24) return BigInt(cur.u8());
  if (ai === 25) {
    const b = cur.take(2);
    return BigInt((b[0]! << 8) | b[1]!);
  }
  if (ai === 26) {
    const b = cur.take(4);
    return BigInt((b[0]! << 24) | (b[1]! << 16) | (b[2]! << 8) | b[3]! >>> 0);
  }
  if (ai === 27) {
    const b = cur.take(8);
    let x = 0n;
    for (let i = 0; i < 8; i++) x = (x << 8n) | BigInt(b[i]!);
    return x;
  }
  throw new RangeError('decode: invalid additional info for length');
}

// Half-precision float (IEEE 754 binary16) → number
function halfToFloat(h: number): number {
  const s = (h & 0x8000) >> 15;
  const e = (h & 0x7C00) >> 10;
  const f = h & 0x03FF;
  if (e === 0) {
    return (s ? -1 : 1) * Math.pow(2, -14) * (f / Math.pow(2, 10));
  } else if (e === 0x1F) {
    return f ? NaN : ((s ? -1 : 1) * Infinity);
  } else {
    return (s ? -1 : 1) * Math.pow(2, e - 15) * (1 + f / Math.pow(2, 10));
  }
}

function bigToJs(n: bigint, opts?: DecodeOptions): number | bigint | string {
  if (n <= BigInt(Number.MAX_SAFE_INTEGER)) return Number(n);
  return opts?.bigintAsString ? n.toString(10) : n;
}

/** Decodes CBOR to JS values. */
export function decode(bytes: ArrayLike<number>, opts?: DecodeOptions): any {
  const cur = new Cursor(bytes instanceof Uint8Array ? bytes : new Uint8Array(bytes as any));
  const val = decAny(cur, opts);
  if (!cur.eof()) throw new RangeError('decode: extraneous bytes at end');
  return val;
}

function decAny(cur: Cursor, opts?: DecodeOptions): any {
  const ib = cur.u8();
  const major = ib >> 5;
  const ai = ib & 31;

  switch (major as Major) {
    case Major.Unsigned: {
      const n = readLen(ai, cur);
      return bigToJs(n, opts);
    }
    case Major.Negative: {
      const n = readLen(ai, cur);
      // value is -1 - n
      const val = -1n - n;
      if (val >= BigInt(-Number.MAX_SAFE_INTEGER)) {
        const num = Number(val);
        if (Number.isSafeInteger(num)) return num;
      }
      return opts?.bigintAsString ? val.toString(10) : val;
    }
    case Major.Bytes: {
      const len = readLen(ai, cur);
      if (len > BigInt(0x7fffffff)) throw new RangeError('decode: absurd byte string length');
      return cur.take(Number(len));
    }
    case Major.Text: {
      const len = readLen(ai, cur);
      if (len > BigInt(0x7fffffff)) throw new RangeError('decode: absurd text length');
      const b = cur.take(Number(len));
      return bytesToUtf8(b);
    }
    case Major.Array: {
      const len = readLen(ai, cur);
      const out = new Array(Number(len));
      for (let i = 0; i < Number(len); i++) out[i] = decAny(cur, opts);
      return out;
    }
    case Major.Map: {
      const len = readLen(ai, cur);
      const obj: Record<string, any> = {};
      for (let i = 0; i < Number(len); i++) {
        const k = decAny(cur, opts);
        const v = decAny(cur, opts);
        // We coerce keys to string for plain objects. If consumer needs non-string keys, use Map.
        obj[String(k)] = v;
      }
      return obj;
    }
    case Major.Tag: {
      // We don't interpret tags; just decode the tagged value and wrap it.
      const tag = readLen(ai, cur);
      const val = decAny(cur, opts);
      return { _tag: bigToJs(tag, opts), value: val };
    }
    case Major.SimpleFloat: {
      if (ai < 20) {
        // unassigned simple values: return as number code
        return ai;
      }
      if (ai === 20) return false;
      if (ai === 21) return true;
      if (ai === 22) return null;
      if (ai === 23) return undefined;
      if (ai === 24) {
        const simple = cur.u8(); // next byte simple value
        return simple;
      }
      if (ai === 25) {
        const b = cur.take(2);
        const h = (b[0]! << 8) | b[1]!;
        return halfToFloat(h);
      }
      if (ai === 26) {
        const b = cur.take(4);
        const dv = new DataView(b.buffer, b.byteOffset, b.byteLength);
        return dv.getFloat32(0, false);
      }
      if (ai === 27) {
        const b = cur.take(8);
        const dv = new DataView(b.buffer, b.byteOffset, b.byteLength);
        return dv.getFloat64(0, false);
      }
      throw new RangeError('decode: invalid simple/float additional info');
    }
    default:
      throw new RangeError('decode: unknown major type');
  }
}

/* ------------------------------------------------------------------------------------------------
 * Public convenience helpers
 * --------------------------------------------------------------------------------------------- */

/** Encode a map/object with canonical (deterministic) ordering. Alias for encode(obj). */
export function encodeDeterministic(obj: Record<string, any> | Map<any, any>): Uint8Array {
  return encode(obj);
}

/** Convenience: encode a number/bigint as a CBOR unsigned integer item (major 0). */
export function encodeUint(n: number | bigint): Uint8Array {
  if ((typeof n === 'number' && n < 0) || (typeof n === 'bigint' && n < 0n)) {
    throw new RangeError('encodeUint: negative not allowed');
  }
  return encUnsigned(n);
}

/** Convenience: encode a byte string item (major 2). */
export function encodeByteString(b: Uint8Array): Uint8Array {
  return encBytes(b);
}

/** Convenience: encode a text string item (major 3). */
export function encodeTextString(s: string): Uint8Array {
  return encText(s);
}

/** Utility to encode a fixed-width unsigned integer as CBOR byte string (major 2), big-endian. */
export function encodeFixedUIntAsBytes(n: number | bigint, width: number): Uint8Array {
  const be = numberToBeBytes(typeof n === 'number' ? BigInt(n) : n, width);
  return encBytes(be);
}

