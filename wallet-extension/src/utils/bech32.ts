/**
 * Bech32/Bech32m utilities (TypeScript)
 * -----------------------------------------------------------------------------
 * Implements a tiny subset of BIP-0173/BIP-0350 sufficient for Animica
 * addresses. Default HRP is "anim" and we always use Bech32m for payloads.
 *
 * Exports:
 *  - encodeBech32m(hrp, data5): string
 *  - decodeBech32m(addr): { hrp: string; data: number[] }
 *  - toWords(bytes: Uint8Array): number[]        // 8-bit -> 5-bit (pad)
 *  - fromWords(words: number[]): Uint8Array      // 5-bit -> 8-bit (no pad)
 *  - encodeAddress(bytes, hrp?): string          // convenience using toWords
 *  - decodeAddress(addr): { hrp, bytes }
 *  - isValidAddress(addr, hrp?): boolean
 */

const CHARSET = 'qpzry9x8gf2tvdw0s3jn54khce6mua7l';
const CHARSET_REV: Record<string, number> = {};
for (let i = 0; i < CHARSET.length; i++) CHARSET_REV[CHARSET[i]] = i;

const BECH32M_CONST = 0x2bc830a3;
const SEP = '1';
const MAX_LEN = 90;

function polymod(values: number[]): number {
  const GENERATORS = [0x3b6a57b2, 0x26508e6d, 0x1ea119fa, 0x3d4233dd, 0x2a1462b3];
  let chk = 1;
  for (const v of values) {
    const b = chk >>> 25;
    chk = ((chk & 0x1ffffff) << 5) ^ v;
    for (let i = 0; i < 5; i++) {
      if ((b >>> i) & 1) chk ^= GENERATORS[i];
    }
  }
  return chk >>> 0;
}

function hrpExpand(hrp: string): number[] {
  const out: number[] = [];
  for (let i = 0; i < hrp.length; i++) out.push(hrp.charCodeAt(i) >>> 5);
  out.push(0);
  for (let i = 0; i < hrp.length; i++) out.push(hrp.charCodeAt(i) & 31);
  return out;
}

function createChecksumBech32m(hrp: string, data: number[]): number[] {
  const values = [...hrpExpand(hrp), ...data, 0, 0, 0, 0, 0, 0];
  const mod = polymod(values) ^ BECH32M_CONST;
  const ret = [];
  for (let p = 0; p < 6; p++) ret.push((mod >>> (5 * (5 - p))) & 31);
  return ret;
}

function verifyChecksumBech32m(hrp: string, data: number[]): boolean {
  return polymod([...hrpExpand(hrp), ...data]) === BECH32M_CONST;
}

function hasMixedCase(s: string): boolean {
  let lower = false, upper = false;
  for (const ch of s) {
    const lc = ch >= 'a' && ch <= 'z';
    const uc = ch >= 'A' && ch <= 'Z';
    lower = lower || lc;
    upper = upper || uc;
    if (lower && upper) return true;
  }
  return false;
}

/* --------------------------------- Public API -------------------------------- */

export function encodeBech32m(hrp: string, data: number[]): string {
  if (!hrp || hrp.length < 1 || hrp.length > 83) throw new Error('bad hrp length');
  if (hrp !== hrp.toLowerCase()) hrp = hrp.toLowerCase();
  const checksum = createChecksumBech32m(hrp, data);
  const combined = [...data, ...checksum];
  const sb: string[] = [hrp, SEP];
  for (const v of combined) {
    if (v < 0 || v > 31) throw new Error('data value out of range');
    sb.push(CHARSET[v]);
  }
  const out = sb.join('');
  if (out.length > MAX_LEN) throw new Error('bech32m string too long');
  return out;
}

export function decodeBech32m(addr: string): { hrp: string; data: number[] } {
  if (!addr) throw new Error('empty bech32m string');
  if (addr.length > MAX_LEN) throw new Error('too long');
  if (hasMixedCase(addr)) throw new Error('mixed case not allowed');

  const s = addr.toLowerCase();
  const pos = s.lastIndexOf(SEP);
  if (pos < 1 || pos + 7 > s.length) throw new Error('separator position invalid');

  const hrp = s.slice(0, pos);
  const rest = s.slice(pos + 1);
  const data: number[] = [];
  for (const ch of rest) {
    const v = CHARSET_REV[ch];
    if (v === undefined) throw new Error('invalid character');
    data.push(v);
  }
  if (!verifyChecksumBech32m(hrp, data)) throw new Error('invalid bech32m checksum');

  return { hrp, data: data.slice(0, -6) };
}

/** Convert 8-bit bytes → 5-bit words (pads with zeros). */
export function toWords(bytes: Uint8Array): number[] {
  return convertBits(Array.from(bytes), 8, 5, true);
}

/** Convert 5-bit words → 8-bit bytes (no padding allowed). */
export function fromWords(words: number[]): Uint8Array {
  const out = convertBits(words, 5, 8, false);
  return new Uint8Array(out);
}

/** Helper: generic bit conversion used by BIP-0173. */
function convertBits(data: number[], fromBits: number, toBits: number, pad: boolean): number[] {
  let acc = 0;
  let bits = 0;
  const ret: number[] = [];
  const maxv = (1 << toBits) - 1;
  for (const value of data) {
    if (value < 0 || value >>> fromBits !== 0) throw new Error('invalid data range');
    acc = (acc << fromBits) | value;
    bits += fromBits;
    while (bits >= toBits) {
      bits -= toBits;
      ret.push((acc >>> bits) & maxv);
    }
  }
  if (pad) {
    if (bits) ret.push((acc << (toBits - bits)) & maxv);
  } else if (bits >= fromBits || ((acc << (toBits - bits)) & maxv)) {
    throw new Error('invalid padding');
  }
  return ret;
}

/* ------------------------------ Address helpers ------------------------------ */

const DEFAULT_HRP = 'anim';

/**
 * Encode raw address bytes (alg_id || sha3(pubkey)) as Bech32m with default HRP.
 * Accepts either Uint8Array or hex string.
 */
export function encodeAddress(bytes: Uint8Array | string, hrp: string = DEFAULT_HRP): string {
  const raw = typeof bytes === 'string' ? hexToBytes(bytes) : bytes;
  const words = toWords(raw);
  return encodeBech32m(hrp, words);
}

/** Decode address → raw bytes and hrp. Throws if invalid. */
export function decodeAddress(addr: string): { hrp: string; bytes: Uint8Array } {
  const { hrp, data } = decodeBech32m(addr);
  return { hrp, bytes: fromWords(data) };
}

/** Validate address format quickly (optionally enforce HRP). */
export function isValidAddress(addr: string, hrp?: string): boolean {
  try {
    const out = decodeAddress(addr);
    return hrp ? out.hrp === hrp : true;
  } catch {
    return false;
  }
}

/* --------------------------------- Tiny hex --------------------------------- */

function hexToBytes(hex: string): Uint8Array {
  let h = hex.startsWith('0x') ? hex.slice(2) : hex;
  if (h.length % 2) h = '0' + h;
  const out = new Uint8Array(h.length / 2);
  for (let i = 0; i < out.length; i++) out[i] = parseInt(h.slice(i * 2, i * 2 + 2), 16);
  return out;
}

export default {
  encodeBech32m,
  decodeBech32m,
  toWords,
  fromWords,
  encodeAddress,
  decodeAddress,
  isValidAddress,
};
