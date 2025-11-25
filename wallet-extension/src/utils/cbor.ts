/**
 * Minimal deterministic CBOR (RFC 8949) for tx/signbytes
 * -----------------------------------------------------------------------------
 * Features:
 *  - Canonical encoding (a.k.a. "deterministic"): shortest integer form, definite
 *    lengths only, and map keys sorted by the byte-wise order of their CBOR-encoded keys.
 *  - Types: null, boolean, number (safe int), bigint (±2^63-1, ±2^64-1 where applicable),
 *    string (UTF-8), Uint8Array (byte string), arrays, maps (JS objects with string keys).
 *  - Floats, tags, indefinite-length items are NOT supported.
 *
 * Decoding returns:
 *  - integers within Number.MAX_SAFE_INTEGER as number
 *  - larger integers as bigint
 *  - text/bytes/arrays/objects as expected
 */

export type CborValue =
  | null
  | boolean
  | number
  | bigint
  | string
  | Uint8Array
  | CborValue[]
  | { [k: string]: CborValue };

const enc = new TextEncoder();
const dec = new TextDecoder();

/* --------------------------------- Encoder ---------------------------------- */

class Writer {
  private chunks: Uint8Array[] = [];
  private size = 0;

  push(b: Uint8Array) {
    this.chunks.push(b);
    this.size += b.length;
  }
  pushByte(v: number) {
    const u = new Uint8Array(1);
    u[0] = v & 0xff;
    this.push(u);
  }
  pushU16(v: number) {
    const u = new Uint8Array(2);
    u[0] = (v >>> 8) & 0xff;
    u[1] = v & 0xff;
    this.push(u);
  }
  pushU32(v: number) {
    const u = new Uint8Array(4);
    u[0] = (v >>> 24) & 0xff;
    u[1] = (v >>> 16) & 0xff;
    u[2] = (v >>> 8) & 0xff;
    u[3] = v & 0xff;
    this.push(u);
  }
  pushU64(v: bigint) {
    const u = new Uint8Array(8);
    let n = v;
    for (let i = 7; i >= 0; i--) {
      u[i] = Number(n & 0xffn);
      n >>= 8n;
    }
    this.push(u);
  }
  concat(): Uint8Array {
    const out = new Uint8Array(this.size);
    let off = 0;
    for (const c of this.chunks) {
      out.set(c, off);
      off += c.length;
    }
    return out;
  }
}

function writeHead(w: Writer, major: number, ai: number) {
  w.pushByte((major << 5) | ai);
}

function writeUint(w: Writer, major: number, n: number | bigint) {
  // n >= 0 expected. Choose shortest definite-length.
  if (typeof n === 'number') {
    if (!Number.isInteger(n) || n < 0) throw new Error('uint expects non-negative integer');
    if (n < 24) {
      writeHead(w, major, n);
      return;
    }
    if (n <= 0xff) {
      writeHead(w, major, 24);
      w.pushByte(n);
    } else if (n <= 0xffff) {
      writeHead(w, major, 25);
      w.pushU16(n);
    } else if (n <= 0xffffffff) {
      writeHead(w, major, 26);
      w.pushU32(n);
    } else {
      // JS numbers beyond 2^32 still "fit", but risk >2^53 loss. Disallow here to be explicit.
      throw new Error('use bigint for uint > 2^32-1');
    }
  } else {
    if (n < 0n) throw new Error('uint expects non-negative bigint');
    if (n < 24n) {
      writeHead(w, major, Number(n));
      return;
    }
    if (n <= 0xffn) {
      writeHead(w, major, 24);
      w.pushByte(Number(n));
    } else if (n <= 0xffffn) {
      writeHead(w, major, 25);
      w.pushU16(Number(n));
    } else if (n <= 0xffffffffn) {
      writeHead(w, major, 26);
      w.pushU32(Number(n));
    } else if (n <= 0xffffffffffffffffn) {
      writeHead(w, major, 27);
      w.pushU64(n);
    } else {
      throw new Error('uint too large (max 64-bit supported)');
    }
  }
}

function encodeItem(w: Writer, v: CborValue) {
  if (v === null) {
    writeHead(w, 7, 22); // null
    return;
  }
  const t = typeof v;
  if (t === 'boolean') {
    writeHead(w, 7, v ? 21 : 20);
    return;
  }
  if (t === 'number') {
    if (!Number.isFinite(v) || !Number.isInteger(v)) {
      throw new Error('floats not supported; use integers');
    }
    if (v >= 0) {
      writeUint(w, 0, v);
    } else {
      // negative int: value = -1 - n
      const n = BigInt(v);
      const m = -1n - n;
      writeUint(w, 1, m);
    }
    return;
  }
  if (t === 'bigint') {
    if (v >= 0n) writeUint(w, 0, v);
    else writeUint(w, 1, -1n - v);
    return;
  }
  if (t === 'string') {
    const bytes = enc.encode(v);
    writeUint(w, 3, bytes.length);
    w.push(bytes);
    return;
  }
  if (v instanceof Uint8Array) {
    writeUint(w, 2, v.length);
    w.push(v);
    return;
  }
  if (Array.isArray(v)) {
    writeUint(w, 4, v.length);
    for (const el of v) encodeItem(w, el as CborValue);
    return;
  }
  // Object (map) — only string keys supported; canonical key order by CBOR-encoded key
  if (t === 'object') {
    const entries = Object.entries(v as Record<string, CborValue>);
    // Pre-encode keys as CBOR text strings to sort by their CBOR bytewise encoding
    const keyed = entries.map(([k, val]) => {
      const kw = new Writer();
      // encode key as CBOR text
      const kbytes = enc.encode(k);
      writeUint(kw, 3, kbytes.length);
      kw.push(kbytes);
      return { k, val, enc: kw.concat() };
    });
    keyed.sort((a, b) => cmpBytes(a.enc, b.enc));
    writeUint(w, 5, keyed.length);
    for (const it of keyed) {
      // write encoded key bytes directly (saves re-encoding)
      w.push(it.enc);
      encodeItem(w, it.val);
    }
    return;
  }
  throw new Error('unsupported type in CBOR encode');
}

function cmpBytes(a: Uint8Array, b: Uint8Array): number {
  const len = Math.min(a.length, b.length);
  for (let i = 0; i < len; i++) {
    const d = a[i] - b[i];
    if (d) return d;
  }
  return a.length - b.length;
}

/** Encode a value using deterministic CBOR. */
export function encode(value: CborValue): Uint8Array {
  const w = new Writer();
  encodeItem(w, value);
  return w.concat();
}

/** Alias for clarity in tx paths. */
export const encodeCanonical = encode;

/* --------------------------------- Decoder ---------------------------------- */

class Reader {
  private v: DataView;
  private b: Uint8Array;
  pos = 0;
  constructor(private buf: Uint8Array) {
    this.b = buf;
    this.v = new DataView(buf.buffer, buf.byteOffset, buf.byteLength);
  }
  ensure(n: number) {
    if (this.pos + n > this.b.length) throw new Error('CBOR: truncated');
  }
  u8(): number {
    this.ensure(1);
    return this.b[this.pos++];
  }
  u16(): number {
    this.ensure(2);
    const v = (this.b[this.pos] << 8) | this.b[this.pos + 1];
    this.pos += 2;
    return v >>> 0;
  }
  u32(): number {
    this.ensure(4);
    const v =
      (this.b[this.pos] * 2 ** 24) |
      (this.b[this.pos + 1] << 16) |
      (this.b[this.pos + 2] << 8) |
      this.b[this.pos + 3];
    this.pos += 4;
    return v >>> 0;
  }
  u64(): bigint {
    this.ensure(8);
    let x = 0n;
    for (let i = 0; i < 8; i++) x = (x << 8n) | BigInt(this.b[this.pos + i]);
    this.pos += 8;
    return x;
  }
  bytes(n: number): Uint8Array {
    this.ensure(n);
    const out = this.b.subarray(this.pos, this.pos + n);
    this.pos += n;
    return out;
  }
}

function readUint(r: Reader, ai: number): number | bigint {
  if (ai < 24) return ai;
  if (ai === 24) return r.u8();
  if (ai === 25) return r.u16();
  if (ai === 26) return r.u32();
  if (ai === 27) return r.u64();
  throw new Error('indefinite lengths not supported');
}

function decodeItem(r: Reader): CborValue {
  const ib = r.u8();
  const major = ib >>> 5;
  const ai = ib & 31;

  switch (major) {
    case 0: { // unsigned int
      const u = readUint(r, ai);
      if (typeof u === 'number') return u;
      // big uint
      if (u <= BigInt(Number.MAX_SAFE_INTEGER)) return Number(u);
      return u;
    }
    case 1: { // negative int
      const u = readUint(r, ai);
      const n = typeof u === 'number' ? BigInt(u) : u;
      const val = -1n - n;
      if (val >= BigInt(-Number.MAX_SAFE_INTEGER) && val <= BigInt(Number.MAX_SAFE_INTEGER)) {
        return Number(val);
      }
      return val;
    }
    case 2: { // byte string
      const len = readUint(r, ai);
      const n = typeof len === 'number' ? len : numberOrThrow(len);
      return r.bytes(n);
    }
    case 3: { // text string
      const len = readUint(r, ai);
      const n = typeof len === 'number' ? len : numberOrThrow(len);
      const bytes = r.bytes(n);
      return dec.decode(bytes);
    }
    case 4: { // array
      const len = readUint(r, ai);
      const n = typeof len === 'number' ? len : numberOrThrow(len);
      const arr: CborValue[] = [];
      for (let i = 0; i < n; i++) arr.push(decodeItem(r));
      return arr;
    }
    case 5: { // map
      const len = readUint(r, ai);
      const n = typeof len === 'number' ? len : numberOrThrow(len);
      const obj: Record<string, CborValue> = {};
      for (let i = 0; i < n; i++) {
        const k = decodeItem(r);
        if (typeof k !== 'string') throw new Error('only text-string map keys supported');
        const v = decodeItem(r);
        obj[k] = v;
      }
      return obj;
    }
    case 6:
      throw new Error('tags not supported');
    case 7: {
      if (ai === 20) return false;
      if (ai === 21) return true;
      if (ai === 22) return null;
      if (ai === 23) return undefined as any; // rarely used
      throw new Error('unsupported simple/float');
    }
    default:
      throw new Error('invalid major type');
  }
}

function numberOrThrow(bi: bigint): number {
  if (bi > BigInt(Number.MAX_SAFE_INTEGER)) throw new Error('length too large');
  return Number(bi);
}

/** Decode CBOR bytes produced by this module. */
export function decode(bytes: Uint8Array): CborValue {
  const r = new Reader(bytes);
  const val = decodeItem(r);
  if (r.pos !== bytes.length) throw new Error('trailing bytes');
  return val;
}

/* ----------------------------- Convenience utils ---------------------------- */

export function toHex(b: Uint8Array): string {
  let s = '0x';
  for (let i = 0; i < b.length; i++) s += b[i].toString(16).padStart(2, '0');
  return s;
}

export function fromHex(hex: string): Uint8Array {
  let h = hex.startsWith('0x') ? hex.slice(2) : hex;
  if (h.length % 2) h = '0' + h;
  const out = new Uint8Array(h.length / 2);
  for (let i = 0; i < out.length; i++) out[i] = parseInt(h.slice(i * 2, i * 2 + 2), 16);
  return out;
}

/* ---------------------------------- Examples ---------------------------------

// Deterministic encoding of a sign-bytes object:
const signBytes = {
  domain: 'animica-tx-v1',
  chainId: 1337,
  nonce: 1,
  tx: new Uint8Array([1,2,3]),
};
const cbor = encode(signBytes);
const roundtrip = decode(cbor);

-------------------------------------------------------------------------------- */

export default { encode, encodeCanonical, decode, toHex, fromHex };
