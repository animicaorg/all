/**
 * cbor.ts
 * -------
 * Minimal, deterministic CBOR (RFC 7049/8949) encode/decode utilities.
 * - Definite-length encoding only
 * - Canonical map key order (lexicographic by UTF-8 string key)
 * - Supports: integers (u/i up to 64-bit), floats (f64), bytes, text, arrays, maps,
 *             true/false/null/undefined
 *
 * This is intentionally small and dependency-free for browser/WASM use.
 */

import { isHexPrefixed, hexToBytes, bytesToUtf8, utf8ToBytes, isBytes } from "./bytes";

/* --------------------------------- Writer --------------------------------- */

class Writer {
  private buf: Uint8Array;
  private view: DataView;
  private off = 0;

  constructor(initial = 256) {
    this.buf = new Uint8Array(initial);
    this.view = new DataView(this.buf.buffer);
  }

  private ensure(n: number) {
    if (this.off + n <= this.buf.length) return;
    let size = this.buf.length;
    while (this.off + n > size) size *= 2;
    const nb = new Uint8Array(size);
    nb.set(this.buf);
    this.buf = nb;
    this.view = new DataView(this.buf.buffer);
  }

  pushByte(b: number) {
    this.ensure(1);
    this.buf[this.off++] = b & 0xff;
  }

  pushBytes(arr: Uint8Array) {
    this.ensure(arr.length);
    this.buf.set(arr, this.off);
    this.off += arr.length;
  }

  pushU8(v: number) {
    this.ensure(1);
    this.view.setUint8(this.off, v);
    this.off += 1;
  }
  pushU16(v: number) {
    this.ensure(2);
    this.view.setUint16(this.off, v, false);
    this.off += 2;
  }
  pushU32(v: number) {
    this.ensure(4);
    this.view.setUint32(this.off, v, false);
    this.off += 4;
  }
  pushU64(v: bigint) {
    this.ensure(8);
    // big-endian
    this.view.setUint32(this.off, Number((v >> 32n) & 0xffffffffn), false);
    this.view.setUint32(this.off + 4, Number(v & 0xffffffffn), false);
    this.off += 8;
  }
  pushF64(v: number) {
    this.ensure(8);
    this.view.setFloat64(this.off, v, false);
    this.off += 8;
  }

  bytes(): Uint8Array {
    return this.buf.subarray(0, this.off);
  }
}

function writeTypeLen(w: Writer, major: number, len: number | bigint) {
  // canonical form: choose smallest additional information
  if (typeof len === "number") {
    if (len < 24) {
      w.pushByte((major << 5) | len);
    } else if (len <= 0xff) {
      w.pushByte((major << 5) | 24);
      w.pushU8(len);
    } else if (len <= 0xffff) {
      w.pushByte((major << 5) | 25);
      w.pushU16(len);
    } else if (len <= 0xffffffff) {
      w.pushByte((major << 5) | 26);
      w.pushU32(len);
    } else {
      w.pushByte((major << 5) | 27);
      w.pushU64(BigInt(len));
    }
  } else {
    // bigint path (up to 64-bit)
    if (len <= 0xffff_ffffn) {
      const n = Number(len);
      if (n < 24) {
        w.pushByte((major << 5) | n);
      } else if (n <= 0xff) {
        w.pushByte((major << 5) | 24);
        w.pushU8(n);
      } else if (n <= 0xffff) {
        w.pushByte((major << 5) | 25);
        w.pushU16(n);
      } else {
        w.pushByte((major << 5) | 26);
        w.pushU32(n);
      }
    } else {
      w.pushByte((major << 5) | 27);
      w.pushU64(len);
    }
  }
}

/* --------------------------------- Encode --------------------------------- */

function encodeUnsigned(w: Writer, x: number | bigint) {
  const n = typeof x === "bigint" ? x : BigInt(x >>> 0);
  writeTypeLen(w, 0, n);
}
function encodeNegative(w: Writer, x: number | bigint) {
  // value = -1 - N
  const N = typeof x === "bigint" ? (-1n - x) : BigInt(-1 - Math.trunc(x));
  writeTypeLen(w, 1, N);
}

function encodeBytes(w: Writer, b: Uint8Array) {
  writeTypeLen(w, 2, b.length);
  w.pushBytes(b);
}

function encodeText(w: Writer, s: string) {
  const b = utf8ToBytes(s);
  writeTypeLen(w, 3, b.length);
  w.pushBytes(b);
}

function encodeArray(w: Writer, arr: any[]) {
  writeTypeLen(w, 4, arr.length);
  for (const v of arr) encodeAny(w, v);
}

function encodeMap(w: Writer, obj: Record<string, any>) {
  // Canonical: sort keys by UTF-8 lexicographic ordering
  const keys = Object.keys(obj).sort();
  writeTypeLen(w, 5, keys.length);
  for (const k of keys) {
    encodeText(w, k);
    encodeAny(w, obj[k]);
  }
}

function isPlainObject(v: any): v is Record<string, any> {
  return v !== null && typeof v === "object" && Object.getPrototypeOf(v) === Object.prototype;
}

function encodeAny(w: Writer, v: any) {
  if (v === null) return w.pushByte(0xf6);
  if (v === undefined) return w.pushByte(0xf7);
  const t = typeof v;

  if (t === "boolean") return w.pushByte(v ? 0xf5 : 0xf4);

  if (t === "number") {
    if (Number.isFinite(v) && Math.trunc(v) === v) {
      if (v >= 0) return encodeUnsigned(w, v);
      return encodeNegative(w, v);
    }
    // float64
    w.pushByte(0xfb);
    return w.pushF64(v);
  }

  if (t === "bigint") {
    if (v >= 0) return encodeUnsigned(w, v);
    return encodeNegative(w, v);
  }

  if (t === "string") return encodeText(w, v);

  if (isBytes(v)) return encodeBytes(w, v);

  if (Array.isArray(v)) return encodeArray(w, v);

  if (isPlainObject(v)) return encodeMap(w, v as Record<string, any>);

  // Fallback: JSON.stringify then encode as text (stable but not ideal)
  return encodeText(w, JSON.stringify(v));
}

export function encodeCbor(value: any): Uint8Array {
  const w = new Writer();
  encodeAny(w, value);
  return w.bytes();
}

/* --------------------------------- Decode --------------------------------- */

class Reader {
  private view: DataView;
  private buf: Uint8Array;
  off = 0;

  constructor(bytes: Uint8Array) {
    this.buf = bytes;
    this.view = new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength);
  }

  readByte(): number {
    if (this.off >= this.buf.length) throw new Error("CBOR: unexpected EOF");
    return this.buf[this.off++];
  }

  readLen(ai: number): number | bigint {
    if (ai < 24) return ai;
    if (ai === 24) return this.view.getUint8(this.bump(1));
    if (ai === 25) return this.view.getUint16(this.bump(2), false);
    if (ai === 26) return this.view.getUint32(this.bump(4), false);
    if (ai === 27) {
      const hi = this.view.getUint32(this.bump(8), false);
      const lo = this.view.getUint32(this.off - 4, false);
      return (BigInt(hi) << 32n) | BigInt(lo);
    }
    throw new Error(`CBOR: invalid additional info ${ai}`);
  }

  readBytes(n: number): Uint8Array {
    if (this.off + n > this.buf.length) throw new Error("CBOR: unexpected EOF (bytes)");
    const out = this.buf.subarray(this.off, this.off + n);
    this.off += n;
    return out;
  }

  private bump(n: number): number {
    const p = this.off;
    if (p + n > this.buf.length) throw new Error("CBOR: unexpected EOF");
    this.off += n;
    return p;
  }
}

function toSafeNumber(x: number | bigint): number | bigint {
  if (typeof x === "bigint") return x;
  return x;
}

function decodeAny(r: Reader): any {
  const ib = r.readByte();
  const major = ib >> 5;
  const ai = ib & 0x1f;

  switch (major) {
    case 0: { // unsigned
      const n = r.readLen(ai);
      return toSafeNumber(n);
    }
    case 1: { // negative
      const n = r.readLen(ai);
      if (typeof n === "bigint") return -1n - n;
      return -1 - n;
    }
    case 2: { // byte string
      const n = r.readLen(ai);
      if (typeof n !== "number") throw new Error("CBOR: bytes length too large");
      return r.readBytes(n);
    }
    case 3: { // text string (UTF-8)
      const n = r.readLen(ai);
      if (typeof n !== "number") throw new Error("CBOR: text length too large");
      return bytesToUtf8(r.readBytes(n));
    }
    case 4: { // array
      const n = r.readLen(ai);
      const len = typeof n === "number" ? n : Number(n);
      const arr = new Array(len);
      for (let i = 0; i < len; i++) arr[i] = decodeAny(r);
      return arr;
    }
    case 5: { // map
      const n = r.readLen(ai);
      const len = typeof n === "number" ? n : Number(n);
      const obj: Record<string, any> = {};
      for (let i = 0; i < len; i++) {
        const k = decodeAny(r);
        const v = decodeAny(r);
        // We mostly encode string keys; if not string, coerce to JSON-ish key
        const keyStr = typeof k === "string" ? k : JSON.stringify(k);
        obj[keyStr] = v;
      }
      return obj;
    }
    case 6: { // tag — ignore tag, decode value
      /* const tag = */ r.readLen(ai);
      return decodeAny(r);
    }
    case 7: {
      if (ai === 20) return false;
      if (ai === 21) return true;
      if (ai === 22) return null;
      if (ai === 23) return undefined;
      if (ai === 24) {
        // simple value (1 byte) — treat as undefined-ish
        r.readLen(ai); // consume
        return undefined;
      }
      if (ai === 26) {
        // float32
        const p = (r as any).bump ? (r as any).bump(4) : null;
        if (p === null) throw new Error("CBOR: internal");
        // Use DataView directly via private, but we avoid TS access
        throw new Error("CBOR: float32 not implemented in minimal decoder");
      }
      if (ai === 27) {
        // float64
        const p = (r as any).off;
        (r as any).off += 8;
        const view: DataView = (r as any).view;
        return view.getFloat64(p, false);
      }
      throw new Error(`CBOR: unsupported simple/float ai=${ai}`);
    }
    default:
      throw new Error(`CBOR: unknown major type ${major}`);
  }
}

export function decodeCbor(input: Uint8Array | string): any {
  const bytes = typeof input === "string"
    ? (isHexPrefixed(input) ? hexToBytes(input) : (() => { throw new Error("decodeCbor expects Uint8Array or 0x-hex string"); })())
    : input;
  const r = new Reader(bytes);
  const v = decodeAny(r);
  if (r.off !== bytes.length) {
    // trailing bytes are suspicious; keep strict
    throw new Error("CBOR: trailing bytes after value");
  }
  return v;
}

/* ------------------------------- Convenience ------------------------------- */

/**
 * Encodes a plain object with canonical key ordering.
 * Equivalent to encodeCbor(obj) but explicit for readability.
 */
export function encodeCanonicalMap(obj: Record<string, any>): Uint8Array {
  const w = new Writer();
  encodeMap(w, obj);
  return w.bytes();
}

export default {
  encodeCbor,
  decodeCbor,
  encodeCanonicalMap,
};
