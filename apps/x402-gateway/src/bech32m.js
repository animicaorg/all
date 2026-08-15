'use strict';
/**
 * Minimal bech32m (BIP-350) codec — enough to turn a customer-facing
 * `anim1…` address into the 32-byte account digest that appears as tx
 * `from`/`to` on-chain. Animica payload layout (verified in
 * rpc/methods/state.py::_to_account_key_bytes and explorer2 normalize.ts):
 *   payload = alg_id (2 bytes) || sha3_256(pubkey) (32 bytes)
 * and the canonical join key is payload[2:34] — the digest. The alg_id is
 * NOT recoverable from a digest, so responses echo the caller's input form
 * and matching happens on the digest only.
 *
 * bech32m only (constant 0x2bc830a3): plain-bech32 checksums fail on this
 * chain by design.
 */

const CHARSET = 'qpzry9x8gf2tvdw0s3jn54khce6mua7l';
const BECH32M_CONST = 0x2bc830a3;

function polymod(values) {
  const GEN = [0x3b6a57b2, 0x26508e6d, 0x1ea119fa, 0x3d4233dd, 0x2a1462b3];
  let chk = 1;
  for (const v of values) {
    const b = chk >>> 25;
    chk = ((chk & 0x1ffffff) << 5) ^ v;
    for (let i = 0; i < 5; i++) if ((b >>> i) & 1) chk ^= GEN[i];
  }
  return chk >>> 0;
}

function hrpExpand(hrp) {
  const out = [];
  for (const c of hrp) out.push(c.charCodeAt(0) >>> 5);
  out.push(0);
  for (const c of hrp) out.push(c.charCodeAt(0) & 31);
  return out;
}

function convertBits(data, from, to, pad) {
  let acc = 0;
  let bits = 0;
  const out = [];
  const maxv = (1 << to) - 1;
  for (const value of data) {
    if (value < 0 || value >>> from !== 0) return null;
    acc = ((acc << from) | value) >>> 0;
    bits += from;
    while (bits >= to) {
      bits -= to;
      out.push((acc >>> bits) & maxv);
    }
  }
  if (pad) {
    if (bits > 0) out.push((acc << (to - bits)) & maxv);
  } else if (bits >= from || ((acc << (to - bits)) & maxv)) {
    return null;
  }
  return out;
}

/** decode('anim1…') -> { hrp, payload: Buffer } ; throws on any defect. */
function decode(addr) {
  if (typeof addr !== 'string' || addr.length < 8 || addr.length > 120) throw new Error('bech32m: bad length');
  const lowered = addr.toLowerCase();
  if (lowered !== addr && addr.toUpperCase() !== addr) throw new Error('bech32m: mixed case');
  const pos = lowered.lastIndexOf('1');
  if (pos < 1 || pos + 7 > lowered.length) throw new Error('bech32m: missing separator');
  const hrp = lowered.slice(0, pos);
  const data = [];
  for (const c of lowered.slice(pos + 1)) {
    const d = CHARSET.indexOf(c);
    if (d === -1) throw new Error(`bech32m: invalid character ${JSON.stringify(c)}`);
    data.push(d);
  }
  if (polymod(hrpExpand(hrp).concat(data)) !== BECH32M_CONST) throw new Error('bech32m: checksum mismatch');
  const payload = convertBits(data.slice(0, -6), 5, 8, false);
  if (payload === null) throw new Error('bech32m: bad padding');
  return { hrp, payload: Buffer.from(payload) };
}

/** encode(hrp, payloadBytes) -> address string (used by tests/tools). */
function encode(hrp, payload) {
  const data = convertBits(Array.from(payload), 8, 5, true);
  const values = hrpExpand(hrp).concat(data);
  const mod = polymod(values.concat([0, 0, 0, 0, 0, 0])) ^ BECH32M_CONST;
  const checksum = [];
  for (let i = 0; i < 6; i++) checksum.push((mod >>> (5 * (5 - i))) & 31);
  return hrp + '1' + data.concat(checksum).map((d) => CHARSET[d]).join('');
}

/**
 * Canonical account-digest hex (no 0x) from any accepted address form:
 * `anim1…` bech32m (34-byte payload: 2-byte alg id + 32-byte digest) or
 * 0x-prefixed/bare 64-hex digest. Throws with a precise message otherwise.
 */
function toAccountDigestHex(input) {
  const s = String(input || '').trim();
  if (/^(0x)?[0-9a-fA-F]{64}$/.test(s)) return s.replace(/^0x/, '').toLowerCase();
  if (s.toLowerCase().startsWith('anim1')) {
    const { hrp, payload } = decode(s);
    if (hrp !== 'anim') throw new Error(`address hrp ${JSON.stringify(hrp)} is not "anim"`);
    if (payload.length !== 34) throw new Error(`anim1 payload must be 34 bytes (alg id + digest), got ${payload.length}`);
    return payload.subarray(2).toString('hex');
  }
  throw new Error('address must be anim1… bech32m or a 32-byte hex account digest');
}

module.exports = { decode, encode, toAccountDigestHex };
