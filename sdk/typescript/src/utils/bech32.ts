/**
 * bech32 / bech32m utilities (BIP-0173 / BIP-0350)
 *
 * - Supports encoding/decoding for both variants; default is bech32m.
 * - Includes 8↔5 bit group converters for payload <-> words transformation.
 * - Enforces lowercase output; rejects mixed-case inputs per spec.
 *
 * Typical address flow (outside this module):
 *   const words = bytesToWords(pubkeyHashBytes)  // 8→5 bits
 *   const addr  = encodeBech32m('anim', words)
 *
 *   const { hrp, words } = decodeBech32m(addr)
 *   const bytes = wordsToBytes(words)            // 5→8 bits
 */

export type Encoding = 'bech32' | 'bech32m'

const CHARSET = 'qpzry9x8gf2tvdw0s3jn54khce6mua7l'
const CHARSET_REV: Record<string, number> = (() => {
  const m: Record<string, number> = {}
  for (let i = 0; i < CHARSET.length; i++) m[CHARSET[i]] = i
  return m
})()

const GENERATOR = [0x3b6a57b2, 0x26508e6d, 0x1ea119fa, 0x3d4233dd, 0x2a1462b3]
const BECH32_CONST = 1
const BECH32M_CONST = 0x2bc830a3

/** Expand HRP for checksum computation. */
function hrpExpand(hrp: string): number[] {
  const ret: number[] = []
  for (let i = 0; i < hrp.length; i++) ret.push(hrp.charCodeAt(i) >> 5)
  ret.push(0)
  for (let i = 0; i < hrp.length; i++) ret.push(hrp.charCodeAt(i) & 31)
  return ret
}

/** Internal polymod over 5-bit values. */
function polymod(values: number[]): number {
  let chk = 1
  for (const v of values) {
    const top = chk >>> 25
    chk = ((chk & 0x1ffffff) << 5) ^ v
    for (let i = 0; i < 5; i++) {
      if ((top >>> i) & 1) chk ^= GENERATOR[i]
    }
  }
  return chk >>> 0
}

function createChecksum(hrp: string, data: number[], enc: Encoding): number[] {
  const constVal = enc === 'bech32' ? BECH32_CONST : BECH32M_CONST
  const values = [...hrpExpand(hrp), ...data, 0, 0, 0, 0, 0, 0]
  const mod = polymod(values) ^ constVal
  const ret = []
  for (let p = 0; p < 6; p++) ret.push((mod >>> (5 * (5 - p))) & 31)
  return ret
}

function verifyChecksum(hrp: string, data: number[]): Encoding | null {
  const pm = polymod([...hrpExpand(hrp), ...data])
  if (pm === BECH32_CONST) return 'bech32'
  if (pm === BECH32M_CONST) return 'bech32m'
  return null
}

function isValidHrp(hrp: string): boolean {
  if (hrp.length < 1 || hrp.length > 83) return false
  for (let i = 0; i < hrp.length; i++) {
    const c = hrp.charCodeAt(i)
    if (c < 33 || c > 126) return false
    // Must be lowercase for normalized form
    if (hrp[i] !== hrp[i].toLowerCase()) return false
  }
  return true
}

function isValidWords(words: number[]): boolean {
  return words.every((w) => Number.isInteger(w) && w >= 0 && w <= 31)
}

/**
 * Encode HRP + words → bech32/bech32m string (lowercase).
 */
export function encode(hrp: string, words: number[], enc: Encoding = 'bech32m'): string {
  if (!isValidHrp(hrp)) throw new Error('bech32: invalid hrp (must be printable ASCII, lowercase, 1..83)')
  if (!isValidWords(words)) throw new Error('bech32: words must be 5-bit integers (0..31)')
  const checksum = createChecksum(hrp, words, enc)
  const encData = [...words, ...checksum].map((v) => CHARSET[v]).join('')
  const out = `${hrp}1${encData}`
  if (out.length > 90) throw new Error('bech32: result too long (>90 chars)')
  return out
}

/** Encode using bech32m constant (BIP-0350). */
export function encodeBech32m(hrp: string, words: number[]): string {
  return encode(hrp, words, 'bech32m')
}

/** Encode using legacy bech32 constant (rarely needed). */
export function encodeBech32(hrp: string, words: number[]): string {
  return encode(hrp, words, 'bech32')
}

/**
 * Decode a bech32/bech32m string. If `expect` provided, must match detected encoding.
 * Returns `{ hrp, words, encoding }`.
 */
export function decode(addr: string, expect?: Encoding | 'auto'): { hrp: string; words: number[]; encoding: Encoding } {
  if (addr.length < 8 || addr.length > 90) throw new Error('bech32: invalid length')
  // Mixed-case check
  const hasLower = addr.toLowerCase() === addr
  const hasUpper = addr.toUpperCase() === addr
  if (!hasLower && !hasUpper) throw new Error('bech32: mixed case not allowed')
  // Normalize to lowercase for processing
  const s = addr.toLowerCase()
  const pos = s.lastIndexOf('1')
  if (pos < 1 || pos + 7 > s.length) throw new Error('bech32: separator position invalid')
  const hrp = s.slice(0, pos)
  const dataPart = s.slice(pos + 1)
  if (!isValidHrp(hrp)) throw new Error('bech32: invalid hrp')
  const words: number[] = []
  for (let i = 0; i < dataPart.length; i++) {
    const c = dataPart[i]
    const v = CHARSET_REV[c]
    if (v === undefined) throw new Error(`bech32: invalid character "${c}" at position ${i}`)
    words.push(v)
  }
  const encDetected = verifyChecksum(hrp, words)
  if (!encDetected) throw new Error('bech32: invalid checksum')
  if (expect && expect !== 'auto' && expect !== encDetected) {
    throw new Error(`bech32: encoding mismatch (expected ${expect}, got ${encDetected})`)
  }
  return { hrp, words: words.slice(0, -6), encoding: encDetected }
}

/** Decode assuming bech32m; throws if not bech32m. */
export function decodeBech32m(addr: string): { hrp: string; words: number[] } {
  const { hrp, words, encoding } = decode(addr, 'bech32m')
  if (encoding !== 'bech32m') throw new Error('bech32m: checksum mismatch')
  return { hrp, words }
}

/** Convert between bit groups; e.g., 8→5 (bytes to words) or 5→8 (words to bytes). */
export function convertBits(
  data: ArrayLike<number>,
  from: number,
  to: number,
  pad = true
): number[] {
  if (from <= 0 || to <= 0) throw new Error('convertBits: invalid from/to')
  let acc = 0
  let bits = 0
  const ret: number[] = []
  const maxv = (1 << to) - 1
  for (let i = 0; i < data.length; i++) {
    const v = data[i]
    if (v < 0 || v >> from !== 0) throw new Error('convertBits: value out of range')
    acc = (acc << from) | v
    bits += from
    while (bits >= to) {
      bits -= to
      ret.push((acc >> bits) & maxv)
    }
  }
  if (pad) {
    if (bits) ret.push((acc << (to - bits)) & maxv)
  } else if (bits >= from || ((acc << (to - bits)) & maxv)) {
    throw new Error('convertBits: non-zero padding')
  }
  return ret
}

/** Convenience: bytes (8-bit) → words (5-bit). */
export function bytesToWords(bytes: Uint8Array): number[] {
  return convertBits(bytes, 8, 5, true)
}

/** Convenience: words (5-bit) → bytes (8-bit). */
export function wordsToBytes(words: number[]): Uint8Array {
  const out = convertBits(words, 5, 8, false)
  return new Uint8Array(out)
}

/** Quick validator (bech32 or bech32m); returns detected encoding or null. */
export function validate(addr: string): Encoding | null {
  try {
    return decode(addr).encoding
  } catch {
    return null
  }
}

export default {
  encode,
  encodeBech32,
  encodeBech32m,
  decode,
  decodeBech32m,
  convertBits,
  bytesToWords,
  wordsToBytes,
  validate
}
