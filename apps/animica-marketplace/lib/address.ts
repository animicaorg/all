// Vendored Animica bech32m address decode (dep-free), mirroring
// packages/launchpad/shared/src/address.ts and the node's pq address format.
// Payload = algId(2B BE) || sha3_256(pubkey) (32B) = 34 bytes. Checksum const bech32m.

const CHARSET = 'qpzry9x8gf2tvdw0s3jn54khce6mua7l';
const CHARSET_REV: Record<string, number> = (() => {
  const out: Record<string, number> = {};
  for (let i = 0; i < CHARSET.length; i += 1) out[CHARSET[i]] = i;
  return out;
})();
const BECH32M_CONST = 0x2bc830a3;
const DEFAULT_HRP = 'anim';
const PAYLOAD_LENGTH = 34;

function polymod(values: number[]): number {
  const GEN = [0x3b6a57b2, 0x26508e6d, 0x1ea119fa, 0x3d4233dd, 0x2a1462b3];
  let chk = 1;
  for (const v of values) {
    const top = chk >>> 25;
    chk = ((chk & 0x1ffffff) << 5) ^ v;
    for (let i = 0; i < 5; i += 1) if ((top >> i) & 1) chk ^= GEN[i];
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
function convertBits(data: number[], fromBits: number, toBits: number, pad: boolean): number[] | null {
  let acc = 0, bits = 0;
  const out: number[] = [];
  const maxv = (1 << toBits) - 1;
  for (const value of data) {
    if (value < 0 || value >> fromBits !== 0) return null;
    acc = (acc << fromBits) | value;
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

export interface DecodedAddress {
  hrp: string;
  algId: number;
  digest: Uint8Array; // sha3_256(pubkey)
}

export function decodeAnimicaAddress(input: string, expectedHrp = DEFAULT_HRP): DecodedAddress {
  if (typeof input !== 'string') throw new Error('address must be a string');
  const lower = input.trim().toLowerCase();
  if (lower !== input.trim()) throw new Error('address must be all lowercase');
  const pos = lower.lastIndexOf('1');
  if (pos < 1 || pos + 7 > lower.length) throw new Error('malformed bech32m address');
  const hrp = lower.slice(0, pos);
  if (hrp !== expectedHrp) throw new Error(`unexpected HRP '${hrp}'`);
  const data: number[] = [];
  for (let i = pos + 1; i < lower.length; i += 1) {
    const v = CHARSET_REV[lower[i]];
    if (v === undefined) throw new Error('invalid bech32m character');
    data.push(v);
  }
  if (!verifyChecksum(hrp, data)) throw new Error('bad bech32m checksum');
  const payload = convertBits(data.slice(0, -6), 5, 8, false);
  if (!payload) throw new Error('bad bech32m payload');
  if (payload.length !== PAYLOAD_LENGTH) throw new Error(`bad payload length ${payload.length}`);
  const algId = (payload[0] << 8) | payload[1];
  return { hrp, algId, digest: new Uint8Array(payload.slice(2)) };
}

export function isAnimicaAddress(input: string): boolean {
  try { decodeAnimicaAddress(input); return true; } catch { return false; }
}
