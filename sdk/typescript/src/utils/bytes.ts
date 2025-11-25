/**
 * Byte helpers for Node and browsers (no polyfills required).
 * - Hex/UTF-8/Base64 conversions
 * - Concat/compare/pad/slice
 * - Varint (uvarint) encode/decode (LEB128-style, unsigned)
 * - BigInt ↔ bytes (big-endian by default)
 * - Random bytes using WebCrypto when available
 */

export type BytesLike =
  | Uint8Array
  | ArrayBuffer
  | number[]
  | string // hex "0x.." or plain hex without 0x
  | null
  | undefined

/** Quick type check */
export function isUint8Array(v: unknown): v is Uint8Array {
  return v instanceof Uint8Array
}

/** Return true if string looks like hex (0x.. or naked) and has even length (after 0x strip). */
export function isHexString(s: unknown): s is string {
  if (typeof s !== 'string') return false
  const t = s.startsWith('0x') || s.startsWith('0X') ? s.slice(2) : s
  return t.length % 2 === 0 && /^[0-9a-fA-F]*$/.test(t)
}

/** Strip 0x/0X prefix if present. */
export function strip0x(s: string): string {
  return s.startsWith('0x') || s.startsWith('0X') ? s.slice(2) : s
}

/** Add 0x prefix if absent. */
export function add0x(s: string): string {
  return s.startsWith('0x') || s.startsWith('0X') ? s : `0x${s}`
}

/** Convert hex string (with or without 0x) to Uint8Array. Empty string → empty bytes. */
export function hexToBytes(hex: string): Uint8Array {
  const clean = strip0x(hex)
  if (clean.length === 0) return new Uint8Array()
  if (clean.length % 2 !== 0 || !/^[0-9a-fA-F]+$/.test(clean)) {
    throw new Error(`hexToBytes: invalid hex string length/characters`)
  }
  const out = new Uint8Array(clean.length / 2)
  for (let i = 0; i < out.length; i++) {
    out[i] = parseInt(clean.slice(2 * i, 2 * i + 2), 16)
  }
  return out
}

/** Convert bytes to lowercase hex string with 0x prefix by default. */
export function bytesToHex(bytes: Uint8Array, with0x = true): string {
  const hex = [...bytes].map(b => b.toString(16).padStart(2, '0')).join('')
  return with0x ? `0x${hex}` : hex
}

/** Convert UTF-8 string to bytes. */
export function utf8ToBytes(s: string): Uint8Array {
  return new TextEncoder().encode(s)
}

/** Convert bytes to UTF-8 string. */
export function bytesToUtf8(bytes: Uint8Array): string {
  return new TextDecoder().decode(bytes)
}

/** Base64 encode (URL-safe optional). */
export function bytesToBase64(bytes: Uint8Array, urlSafe = false): string {
  // Prefer Buffer in Node, else btoa in browsers
  let b64: string
  if (typeof Buffer !== 'undefined' && typeof Buffer.from === 'function') {
    b64 = Buffer.from(bytes).toString('base64')
  } else {
    let binary = ''
    for (let i = 0; i < bytes.length; i++) binary += String.fromCharCode(bytes[i])
    // eslint-disable-next-line @typescript-eslint/ban-ts-comment
    // @ts-ignore
    b64 = btoa(binary)
  }
  if (urlSafe) b64 = b64.replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/g, '')
  return b64
}

/** Base64 decode (URL-safe allowed). */
export function base64ToBytes(b64: string): Uint8Array {
  const norm = b64.replace(/-/g, '+').replace(/_/g, '/')
  // pad to multiple of 4
  const pad = norm.length % 4 === 0 ? '' : '='.repeat(4 - (norm.length % 4))
  const s = norm + pad
  if (typeof Buffer !== 'undefined' && typeof Buffer.from === 'function') {
    return new Uint8Array(Buffer.from(s, 'base64'))
  } else {
    // eslint-disable-next-line @typescript-eslint/ban-ts-comment
    // @ts-ignore
    const bin = atob(s)
    const out = new Uint8Array(bin.length)
    for (let i = 0; i < bin.length; i++) out[i] = bin.charCodeAt(i)
    return out
  }
}

/** Normalize BytesLike into a new Uint8Array (copy). */
export function toBytes(v: BytesLike): Uint8Array {
  if (v == null) return new Uint8Array()
  if (isUint8Array(v)) return new Uint8Array(v)
  if (v instanceof ArrayBuffer) return new Uint8Array(v.slice(0))
  if (Array.isArray(v)) return new Uint8Array(v)
  if (typeof v === 'string') {
    if (!isHexString(v)) throw new Error('toBytes: only hex strings are supported')
    return hexToBytes(v)
  }
  throw new Error(`toBytes: unsupported type ${(typeof v)}`)
}

/** Return the same view if already Uint8Array, else allocate new bytes. */
export function asBytes(v: BytesLike): Uint8Array {
  if (isUint8Array(v)) return v
  return toBytes(v)
}

/** Concatenate multiple byte arrays. */
export function concatBytes(list: BytesLike[]): Uint8Array {
  if (list.length === 0) return new Uint8Array()
  const arrays = list.map(asBytes)
  const size = arrays.reduce((n, a) => n + a.length, 0)
  const out = new Uint8Array(size)
  let off = 0
  for (const a of arrays) {
    out.set(a, off)
    off += a.length
  }
  return out
}

/** Constant-time-ish equality (length + byte-wise compare). */
export function equalBytes(a: BytesLike, b: BytesLike): boolean {
  const A = asBytes(a)
  const B = asBytes(b)
  if (A.length !== B.length) return false
  let acc = 0
  for (let i = 0; i < A.length; i++) acc |= (A[i] ^ B[i])
  return acc === 0
}

/** Lexicographic compare: returns -1, 0, 1. */
export function compareBytes(a: BytesLike, b: BytesLike): number {
  const A = asBytes(a)
  const B = asBytes(b)
  const len = Math.min(A.length, B.length)
  for (let i = 0; i < len; i++) {
    if (A[i] < B[i]) return -1
    if (A[i] > B[i]) return 1
  }
  if (A.length === B.length) return 0
  return A.length < B.length ? -1 : 1
}

/** Left-pad with zeros to desired length (truncate if longer and truncate=true). */
export function padLeft(bytes: BytesLike, len: number, truncate = false): Uint8Array {
  const b = asBytes(bytes)
  if (b.length === len) return new Uint8Array(b)
  if (b.length > len) {
    if (!truncate) throw new Error(`padLeft: input longer than target length`)
    return b.slice(b.length - len)
    }
  const out = new Uint8Array(len)
  out.set(b, len - b.length)
  return out
}

/** Right-pad with zeros to desired length (truncate if longer and truncate=true). */
export function padRight(bytes: BytesLike, len: number, truncate = false): Uint8Array {
  const b = asBytes(bytes)
  if (b.length === len) return new Uint8Array(b)
  if (b.length > len) {
    if (!truncate) throw new Error(`padRight: input longer than target length`)
    return b.slice(0, len)
  }
  const out = new Uint8Array(len)
  out.set(b, 0)
  return out
}

/** Exact slice with bounds check. */
export function sliceExact(bytes: BytesLike, start: number, end: number): Uint8Array {
  const b = asBytes(bytes)
  if (start < 0 || end < start || end > b.length) throw new Error('sliceExact: out of bounds')
  return b.slice(start, end)
}

/** Ensure exact length, throwing if mismatch. */
export function assertLength(bytes: BytesLike, len: number, msg?: string): Uint8Array {
  const b = asBytes(bytes)
  if (b.length !== len) throw new Error(msg || `assertLength: expected ${len} bytes, got ${b.length}`)
  return b
}

/** Unsigned varint (LEB128) encode for BigInt/number (0 ≤ x < 2^64 typically). */
export function uvarintEncode(value: number | bigint): Uint8Array {
  let v = typeof value === 'bigint' ? value : BigInt(value >>> 0) // force non-negative
  if (v < 0n) throw new Error('uvarintEncode: negative not allowed')
  const out: number[] = []
  while (v >= 0x80n) {
    out.push(Number((v & 0x7fn) | 0x80n))
    v >>= 7n
  }
  out.push(Number(v))
  return new Uint8Array(out)
}

/** Decode an unsigned varint (LEB128). Returns [value, bytesRead]. */
export function uvarintDecode(bytes: BytesLike, maxBytes = 10): [bigint, number] {
  const b = asBytes(bytes)
  let x = 0n
  let s = 0n
  let i = 0
  while (i < b.length && i < maxBytes) {
    const v = BigInt(b[i])
    if ((v & 0x80n) === 0n) {
      x |= (v & 0x7fn) << s
      return [x, i + 1]
    }
    x |= (v & 0x7fn) << s
    s += 7n
    i += 1
  }
  throw new Error('uvarintDecode: buffer too small or malformed')
}

/** Big-endian bigInt → bytes (minimal or fixed length if `len` given). */
export function bigIntToBytesBE(x: bigint, len?: number): Uint8Array {
  if (x < 0n) throw new Error('bigIntToBytesBE: negative not supported')
  if (x === 0n) return len ? new Uint8Array(len) : new Uint8Array([0])
  const out: number[] = []
  let v = x
  while (v > 0n) {
    out.push(Number(v & 0xffn))
    v >>= 8n
  }
  out.reverse()
  const u = new Uint8Array(out)
  return len != null ? padLeft(u, len, true) : u
}

/** Big-endian bytes → BigInt. */
export function bytesToBigIntBE(bytes: BytesLike): bigint {
  const b = asBytes(bytes)
  let x = 0n
  for (let i = 0; i < b.length; i++) {
    x = (x << 8n) | BigInt(b[i])
  }
  return x
}

/** Little-endian variants (rarely needed for wire formats). */
export function bigIntToBytesLE(x: bigint, len?: number): Uint8Array {
  if (x < 0n) throw new Error('bigIntToBytesLE: negative not supported')
  if (x === 0n) return len ? new Uint8Array(len) : new Uint8Array([0])
  const out: number[] = []
  let v = x
  while (v > 0n) {
    out.push(Number(v & 0xffn))
    v >>= 8n
  }
  const u = new Uint8Array(out)
  return len != null ? padRight(u, len, true) : u
}

export function bytesToBigIntLE(bytes: BytesLike): bigint {
  const b = asBytes(bytes)
  let x = 0n
  for (let i = b.length - 1; i >= 0; i--) {
    x = (x << 8n) | BigInt(b[i])
  }
  return x
}

/** Return cryptographically strong random bytes if possible. */
export function randomBytes(length: number): Uint8Array {
  if (length <= 0) return new Uint8Array()
  // Prefer SubtleCrypto.getRandomValues in both Node >=18 and browsers
  const g: any = globalThis as any
  if (g?.crypto?.getRandomValues) {
    const arr = new Uint8Array(length)
    g.crypto.getRandomValues(arr)
    return arr
  }
  // Fallback to Node:crypto if available (externalized in bundlers)
  try {
    // eslint-disable-next-line @typescript-eslint/no-var-requires
    const { randomBytes: nodeRandomBytes } = require('crypto')
    return new Uint8Array(nodeRandomBytes(length))
  } catch {
    // Last resort: insecure random (only for tests)
    const out = new Uint8Array(length)
    for (let i = 0; i < length; i++) out[i] = Math.floor(Math.random() * 256)
    return out
  }
}

/** Ensure a BytesLike is hex string with 0x prefix. */
export function ensureHex0x(input: BytesLike): string {
  if (typeof input === 'string') {
    if (!isHexString(input)) throw new Error('ensureHex0x: invalid hex string')
    return add0x(strip0x(input))
  }
  return bytesToHex(asBytes(input), true)
}

/** Pretty-print short hex (0x + first 6 + … + last 4). */
export function shortHex(input: BytesLike, head = 6, tail = 4): string {
  const h = typeof input === 'string' ? add0x(strip0x(input).toLowerCase()) : bytesToHex(asBytes(input))
  const s = strip0x(h)
  if (s.length <= head + tail) return h
  return `0x${s.slice(0, head)}…${s.slice(-tail)}`
}
