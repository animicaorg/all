/**
 * Animica bech32m addresses (BIP-0173 / BIP-0350).
 *
 * Format (matches pq/py/address.py + pq/py/utils/bech32.py in the monorepo):
 *   address = bech32m(hrp = "anim", data = convertBits(payload, 8 -> 5))
 *   payload = alg_id (2 bytes big-endian) || sha3_256(pubkey) (32 bytes)
 *
 * Known alg ids: 0x1003 ml_dsa_65 (the live scheme), 0x1002 SPHINCS+ (legacy).
 * This module is self-contained — no runtime dependencies.
 */

export const ANIMICA_HRP = "anim";
/** ML-DSA-65 — the only actively used signature scheme on mainnet. */
export const ALG_ID_ML_DSA_65 = 0x1003;
/** SPHINCS+ (legacy, stranded scheme — do not create new accounts with it). */
export const ALG_ID_SPHINCS_PLUS = 0x1002;

const CHARSET = "qpzry9x8gf2tvdw0s3jn54khce6mua7l";
const CHARSET_REV: Record<string, number> = {};
for (let i = 0; i < CHARSET.length; i++) CHARSET_REV[CHARSET[i]] = i;

const BECH32M_CONST = 0x2bc830a3;
const BECH32_CONST = 1;
const MAX_LENGTH = 90;
/** alg_id (2) + sha3-256 digest (32). */
const ADDRESS_PAYLOAD_LEN = 34;

export type Bech32Spec = "bech32" | "bech32m";

export class AddressError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "AddressError";
  }
}

// ---------------------------------------------------------------------------
// Bech32m primitives (ported from the BIP-0350 reference implementation)
// ---------------------------------------------------------------------------

const GENERATORS = [0x3b6a57b2, 0x26508e6d, 0x1ea119fa, 0x3d4233dd, 0x2a1462b3];

function polymod(values: number[]): number {
  let chk = 1;
  for (const v of values) {
    const top = chk >>> 25;
    chk = ((chk & 0x1ffffff) << 5) ^ v;
    for (let i = 0; i < 5; i++) {
      if ((top >>> i) & 1) chk ^= GENERATORS[i];
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

function verifyChecksum(hrp: string, data: number[]): Bech32Spec | null {
  const c = polymod([...hrpExpand(hrp), ...data]);
  if (c === BECH32_CONST) return "bech32";
  if (c === BECH32M_CONST) return "bech32m";
  return null;
}

function createChecksum(
  hrp: string,
  data: number[],
  spec: Bech32Spec
): number[] {
  const constant = spec === "bech32m" ? BECH32M_CONST : BECH32_CONST;
  const values = [...hrpExpand(hrp), ...data, 0, 0, 0, 0, 0, 0];
  const mod = polymod(values) ^ constant;
  const out: number[] = [];
  for (let i = 0; i < 6; i++) out.push((mod >>> (5 * (5 - i))) & 31);
  return out;
}

/**
 * Encode 5-bit groups under an HRP with a bech32m (default) checksum.
 * Returns an all-lowercase string.
 */
export function bech32mEncode(
  hrp: string,
  data5: ArrayLike<number>,
  spec: Bech32Spec = "bech32m"
): string {
  if (!hrp || hrp.length < 1 || hrp.length > 83) {
    throw new AddressError("invalid HRP length");
  }
  for (let i = 0; i < hrp.length; i++) {
    const c = hrp.charCodeAt(i);
    if (c < 33 || c > 126) throw new AddressError("invalid HRP character");
  }
  const hrpLower = hrp.toLowerCase();
  if (hrp !== hrpLower && hrp !== hrp.toUpperCase()) {
    throw new AddressError("mixed-case HRP");
  }
  const data = Array.from(data5, (v) => {
    if (!Number.isInteger(v) || v < 0 || v > 31) {
      throw new AddressError("data value out of 5-bit range");
    }
    return v;
  });
  const combined = [...data, ...createChecksum(hrpLower, data, spec)];
  const encoded =
    hrpLower + "1" + combined.map((d) => CHARSET[d]).join("");
  if (encoded.length > MAX_LENGTH) {
    throw new AddressError("encoded string exceeds 90 characters");
  }
  return encoded;
}

/**
 * Decode a bech32/bech32m string into { hrp, data (5-bit groups), spec }.
 * Rejects mixed case, bad charset, and bad checksums.
 */
export function bech32mDecode(str: string): {
  hrp: string;
  data: number[];
  spec: Bech32Spec;
} {
  if (typeof str !== "string" || str.length > MAX_LENGTH) {
    throw new AddressError("invalid bech32 string length");
  }
  const lower = str.toLowerCase();
  const upper = str.toUpperCase();
  if (str !== lower && str !== upper) {
    throw new AddressError("mixed-case string");
  }
  const s = lower;
  for (let i = 0; i < s.length; i++) {
    const c = s.charCodeAt(i);
    if (c < 33 || c > 126) throw new AddressError("invalid character");
  }
  const pos = s.lastIndexOf("1");
  if (pos < 1 || pos + 7 > s.length) {
    throw new AddressError("missing or misplaced separator");
  }
  const hrp = s.slice(0, pos);
  const data: number[] = [];
  for (const ch of s.slice(pos + 1)) {
    const v = CHARSET_REV[ch];
    if (v === undefined) throw new AddressError(`invalid data character '${ch}'`);
    data.push(v);
  }
  const spec = verifyChecksum(hrp, data);
  if (spec === null) throw new AddressError("checksum verification failed");
  return { hrp, data: data.slice(0, -6), spec };
}

/**
 * General power-of-2 base conversion (BIP-0173 convertbits).
 * Encode direction uses pad=true (8→5); decode uses pad=false (5→8).
 */
export function convertBits(
  data: ArrayLike<number>,
  fromBits: number,
  toBits: number,
  pad: boolean
): number[] {
  let acc = 0;
  let bits = 0;
  const out: number[] = [];
  const maxv = (1 << toBits) - 1;
  const maxAcc = (1 << (fromBits + toBits - 1)) - 1;
  for (let i = 0; i < data.length; i++) {
    const value = data[i];
    if (!Number.isInteger(value) || value < 0 || value >> fromBits !== 0) {
      throw new AddressError("invalid value for base conversion");
    }
    acc = ((acc << fromBits) | value) & maxAcc;
    bits += fromBits;
    while (bits >= toBits) {
      bits -= toBits;
      out.push((acc >> bits) & maxv);
    }
  }
  if (pad) {
    if (bits > 0) out.push((acc << (toBits - bits)) & maxv);
  } else if (bits >= fromBits || ((acc << (toBits - bits)) & maxv) !== 0) {
    throw new AddressError("invalid padding in base conversion");
  }
  return out;
}

// ---------------------------------------------------------------------------
// Animica address helpers
// ---------------------------------------------------------------------------

export interface DecodedAddress {
  hrp: string;
  /** Full 34-byte payload: algId (2 bytes BE) || sha3-256(pubkey) digest. */
  payload: Uint8Array;
  /** Signature algorithm id (0x1003 = ml_dsa_65). */
  algId: number;
  /** 32-byte sha3-256(pubkey) digest. */
  digest: Uint8Array;
}

/** Encode a raw payload (typically 34 bytes) into a bech32m anim1… address. */
export function encodeAddress(
  payload: Uint8Array | ArrayLike<number>,
  hrp: string = ANIMICA_HRP
): string {
  const data5 = convertBits(payload, 8, 5, true);
  return bech32mEncode(hrp, data5, "bech32m");
}

/**
 * Decode an anim1… address into its components.
 * Enforces bech32m spec, expected HRP, and the 34-byte payload shape.
 * Throws AddressError on any failure.
 */
export function decodeAddress(
  addr: string,
  expectHrp: string | null = ANIMICA_HRP
): DecodedAddress {
  const { hrp, data, spec } = bech32mDecode(addr);
  if (spec !== "bech32m") {
    throw new AddressError("Animica addresses must use bech32m");
  }
  if (expectHrp !== null && hrp !== expectHrp) {
    throw new AddressError(
      `HRP mismatch: expected '${expectHrp}', got '${hrp}'`
    );
  }
  const payload = Uint8Array.from(convertBits(data, 5, 8, false));
  if (payload.length !== ADDRESS_PAYLOAD_LEN) {
    throw new AddressError(
      `payload length invalid: ${payload.length} != ${ADDRESS_PAYLOAD_LEN}`
    );
  }
  const algId = (payload[0] << 8) | payload[1];
  return { hrp, payload, algId, digest: payload.slice(2) };
}

/**
 * Validate an Animica address. Returns true for a well-formed bech32m
 * 'anim1…' address with a 34-byte payload; false otherwise (never throws).
 */
export function validateAddress(addr: string): boolean {
  try {
    decodeAddress(addr, ANIMICA_HRP);
    return true;
  } catch {
    return false;
  }
}

/** Shorten an address for display: anim1z…946ga */
export function shortAddress(addr: string, keep = 6): string {
  if (addr.length <= 2 * keep + 3) return addr;
  return `${addr.slice(0, keep)}…${addr.slice(-keep)}`;
}
