// Animica bech32m address utilities (mirrors apps/wallet-extension/src/lib/address/animicaAddress.ts).
// Pure TS, no external dependencies — bech32m polynomial inline so the
// shared package stays dep-free.

const CHARSET = "qpzry9x8gf2tvdw0s3jn54khce6mua7l";
const CHARSET_REV: Record<string, number> = (() => {
  const out: Record<string, number> = {};
  for (let i = 0; i < CHARSET.length; i += 1) out[CHARSET[i]] = i;
  return out;
})();

const BECH32M_CONST = 0x2bc830a3;

function polymod(values: number[]): number {
  const GEN = [0x3b6a57b2, 0x26508e6d, 0x1ea119fa, 0x3d4233dd, 0x2a1462b3];
  let chk = 1;
  for (const v of values) {
    const top = chk >>> 25;
    chk = ((chk & 0x1ffffff) << 5) ^ v;
    for (let i = 0; i < 5; i += 1) {
      if ((top >> i) & 1) chk ^= GEN[i];
    }
  }
  return chk >>> 0;
}

function hrpExpand(hrp: string): number[] {
  const out: number[] = [];
  for (let i = 0; i < hrp.length; i += 1) out.push(hrp.charCodeAt(i) >> 5);
  out.push(0);
  for (let i = 0; i < hrp.length; i += 1) out.push(hrp.charCodeAt(i) & 31);
  return out;
}

function verifyChecksum(hrp: string, data: number[]): boolean {
  return polymod(hrpExpand(hrp).concat(data)) === BECH32M_CONST;
}

function createChecksum(hrp: string, data: number[]): number[] {
  const values = hrpExpand(hrp).concat(data).concat([0, 0, 0, 0, 0, 0]);
  const mod = polymod(values) ^ BECH32M_CONST;
  const out: number[] = [];
  for (let i = 0; i < 6; i += 1) out.push((mod >> (5 * (5 - i))) & 31);
  return out;
}

function convertBits(data: Uint8Array | number[], fromBits: number, toBits: number, pad: boolean): number[] | null {
  let acc = 0;
  let bits = 0;
  const out: number[] = [];
  const maxv = (1 << toBits) - 1;
  for (let i = 0; i < data.length; i += 1) {
    const v = data[i];
    if (v < 0 || v >> fromBits !== 0) return null;
    acc = (acc << fromBits) | v;
    bits += fromBits;
    while (bits >= toBits) {
      bits -= toBits;
      out.push((acc >> bits) & maxv);
    }
  }
  if (pad) {
    if (bits > 0) out.push((acc << (toBits - bits)) & maxv);
  } else if (bits >= fromBits || ((acc << (toBits - bits)) & maxv)) {
    return null;
  }
  return out;
}

export interface DecodedAnimAddress {
  hrp: string;
  algId: number;
  digest: Uint8Array;
  payload: Uint8Array;
}

const DEFAULT_HRP = "anim";
const PAYLOAD_LENGTH = 34;

export function isAnimicaAddress(input: string, expectedHrp = DEFAULT_HRP): boolean {
  try {
    decodeAnimicaAddress(input, expectedHrp);
    return true;
  } catch {
    return false;
  }
}

export function decodeAnimicaAddress(input: string, expectedHrp = DEFAULT_HRP): DecodedAnimAddress {
  if (typeof input !== "string") throw new Error("Address must be a string");
  const lower = input.trim().toLowerCase();
  if (lower !== input.trim()) throw new Error("Address must be all lowercase");
  const pos = lower.lastIndexOf("1");
  if (pos < 1 || pos + 7 > lower.length) throw new Error("Malformed bech32m address");
  const hrp = lower.slice(0, pos);
  if (hrp !== expectedHrp) throw new Error(`Unexpected HRP '${hrp}', want '${expectedHrp}'`);
  const data: number[] = [];
  for (let i = pos + 1; i < lower.length; i += 1) {
    const v = CHARSET_REV[lower[i]];
    if (v === undefined) throw new Error("Invalid bech32m character");
    data.push(v);
  }
  if (!verifyChecksum(hrp, data)) throw new Error("Bad bech32m checksum");
  const payload = convertBits(data.slice(0, -6), 5, 8, false);
  if (!payload) throw new Error("Bad bech32m payload");
  if (payload.length !== PAYLOAD_LENGTH) {
    throw new Error(`Bad payload length: ${payload.length}`);
  }
  const algId = (payload[0] << 8) | payload[1];
  const digest = new Uint8Array(payload.slice(2));
  return { hrp, algId, digest, payload: new Uint8Array(payload) };
}

export function encodeAnimicaAddress(payload: Uint8Array, hrp = DEFAULT_HRP): string {
  const data = convertBits(payload, 8, 5, true);
  if (!data) throw new Error("Bad payload");
  const checksum = createChecksum(hrp, data);
  let out = hrp + "1";
  for (const v of data.concat(checksum)) out += CHARSET[v];
  return out;
}

export const ANIM_HRP = DEFAULT_HRP;
export const ANIM_ALG_DILITHIUM3 = 0x1001;
export const ANIM_ALG_SPHINCS_SHAKE_128S = 0x1002;
export const ANIM_SIGN_MESSAGE_DOMAIN_PREFIX = "animica:signMessage:";
