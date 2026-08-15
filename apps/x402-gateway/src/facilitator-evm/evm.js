'use strict';
/**
 * EVM primitives for the self-hosted exact-EVM facilitator: hex handling,
 * keccak-256 / secp256k1 via the audited @noble packages (house rule: never
 * hand-roll curve math or keccak in a payments path), EIP-55 checksums,
 * EIP-712 digest construction, minimal RLP, and EIP-1559 (type-2)
 * transaction encoding/signing.
 *
 * Signature layout note (verified by execution against @noble/secp256k1
 * 3.1.0): noble's "recovered" format is recovery(1) || r(32) || s(32) —
 * recovery byte FIRST. Ethereum wire layout is r(32) || s(32) || v(1) with
 * v in {27,28}. Conversions are explicit at this boundary and nowhere else.
 */

const secp = require('@noble/secp256k1');
const { keccak_256 } = require('@noble/hashes/sha3.js');
const { hmac } = require('@noble/hashes/hmac.js');
const { sha256 } = require('@noble/hashes/sha2.js');

// noble v3 needs hashes wired before sync signing (RFC6979 HMAC-DRBG).
secp.hashes.hmacSha256 = (key, msg) => hmac(sha256, key, msg);
secp.hashes.sha256 = sha256;

const SECP256K1_N = BigInt('0xfffffffffffffffffffffffffffffffebaaedce6af48a03bbfd25e8cd0364141');
const SECP256K1_HALF_N = SECP256K1_N >> 1n;

/* ------------------------------------------------------------------ hex -- */

function strip0x(h) {
  return typeof h === 'string' && h.startsWith('0x') ? h.slice(2) : h;
}

function isHex(s, bytes) {
  if (typeof s !== 'string') return false;
  const h = strip0x(s);
  if (bytes !== undefined && h.length !== bytes * 2) return false;
  return h.length % 2 === 0 && /^[0-9a-fA-F]*$/.test(h);
}

function hexToBytes(h) {
  const s = strip0x(h);
  if (!/^[0-9a-fA-F]*$/.test(s) || s.length % 2 !== 0) throw new Error(`invalid hex: ${JSON.stringify(h)}`);
  return Uint8Array.from(Buffer.from(s, 'hex'));
}

function bytesToHex(b) {
  return '0x' + Buffer.from(b).toString('hex');
}

/** BigInt -> minimal big-endian bytes (empty for 0n) — RLP integer form. */
function bigintToMinimalBytes(v) {
  if (typeof v !== 'bigint') throw new Error('expected bigint');
  if (v < 0n) throw new Error('negative integers cannot be RLP encoded');
  if (v === 0n) return new Uint8Array(0);
  let h = v.toString(16);
  if (h.length % 2) h = '0' + h;
  return hexToBytes(h);
}

/** BigInt -> 32-byte big-endian word (abi encoding). */
function bigintToWord(v) {
  if (typeof v !== 'bigint' || v < 0n) throw new Error('expected non-negative bigint');
  const h = v.toString(16).padStart(64, '0');
  if (h.length > 64) throw new Error('value exceeds 32 bytes');
  return hexToBytes(h);
}

/** Hex quantity string ("0x1a") -> BigInt. Rejects garbage loudly. */
function quantityToBigInt(q) {
  if (typeof q !== 'string' || !/^0x[0-9a-fA-F]+$/.test(q)) {
    throw new Error(`invalid quantity: ${JSON.stringify(q)}`);
  }
  return BigInt(q);
}

/* ------------------------------------------------------------- address -- */

function keccak(bytes) {
  return keccak_256(bytes);
}

/** EIP-55 checksummed rendering of a 20-byte address. */
function toChecksumAddress(addr) {
  const clean = strip0x(String(addr)).toLowerCase();
  if (!/^[0-9a-f]{40}$/.test(clean)) throw new Error(`invalid EVM address: ${JSON.stringify(addr)}`);
  const hash = Buffer.from(keccak_256(Buffer.from(clean, 'ascii'))).toString('hex');
  let out = '0x';
  for (let i = 0; i < 40; i++) {
    out += parseInt(hash[i], 16) >= 8 ? clean[i].toUpperCase() : clean[i];
  }
  return out;
}

function isAddress(addr) {
  return typeof addr === 'string' && /^0x[0-9a-fA-F]{40}$/.test(addr);
}

/**
 * Validate an address; if it carries mixed case the EIP-55 checksum must be
 * correct (all-lower/all-upper is accepted as unchecksummed). Returns the
 * checksummed form.
 */
function validateAddress(addr, what = 'address') {
  if (!isAddress(addr)) throw new Error(`${what} is not a 0x-prefixed 20-byte hex address: ${JSON.stringify(addr)}`);
  const body = addr.slice(2);
  const hasLower = /[a-f]/.test(body);
  const hasUpper = /[A-F]/.test(body);
  const checksummed = toChecksumAddress(addr);
  if (hasLower && hasUpper && addr !== checksummed) {
    throw new Error(`${what} has a bad EIP-55 checksum: ${addr}`);
  }
  return checksummed;
}

function addressEquals(a, b) {
  return isAddress(a) && isAddress(b) && a.toLowerCase() === b.toLowerCase();
}

/* ----------------------------------------------------------------- key -- */

/** 32-byte private key -> 0x address. Throws on out-of-range keys. */
function privateKeyToAddress(privBytes) {
  const pub = secp.getPublicKey(privBytes, false); // 65 bytes, 0x04 prefix
  return toChecksumAddress(bytesToHex(keccak_256(pub.slice(1)).slice(12)));
}

/* -------------------------------------------------------------- EIP-712 -- */

const EIP712_DOMAIN_TYPEHASH = hexToBytes(
  // keccak256("EIP712Domain(string name,string version,uint256 chainId,address verifyingContract)")
  '0x8b73c3c69bb8fe3d512ecc4cf759cc79239f7b179b0ffacaa9a75d522b39400f'
);

/** EIP-712 domain separator for {name, version, chainId, verifyingContract}. */
function domainSeparator({ name, version, chainId, verifyingContract }) {
  const enc = Buffer.concat([
    EIP712_DOMAIN_TYPEHASH,
    keccak_256(Buffer.from(String(name), 'utf8')),
    keccak_256(Buffer.from(String(version), 'utf8')),
    bigintToWord(BigInt(chainId)),
    bigintToWord(BigInt('0x' + strip0x(validateAddress(verifyingContract, 'verifyingContract')))),
  ]);
  return keccak_256(enc);
}

/** digest = keccak256(0x1901 || domainSeparator || structHash) */
function eip712Digest(domainSep, structHash) {
  return keccak_256(Buffer.concat([Buffer.from([0x19, 0x01]), domainSep, structHash]));
}

/* ------------------------------------------------- signatures (eth wire) -- */

/**
 * Parse an Ethereum 65-byte signature hex (r||s||v, v in {27,28}) into
 * {r, s, v, noble} where noble is the recovered-format byte layout noble
 * expects: recovery || r || s.
 */
function parseEthSignature(sigHex) {
  if (!isHex(sigHex, 65)) throw new Error('signature must be 65 bytes of hex');
  const bytes = hexToBytes(sigHex);
  const r = bytes.slice(0, 32);
  const s = bytes.slice(32, 64);
  const v = bytes[64];
  if (v !== 27 && v !== 28) throw new Error(`signature v must be 27 or 28, got ${v}`);
  const sBig = BigInt(bytesToHex(s));
  if (sBig === 0n) throw new Error('signature s is zero');
  if (sBig > SECP256K1_HALF_N) throw new Error('signature s is not low-s (malleable)');
  const rBig = BigInt(bytesToHex(r));
  if (rBig === 0n || rBig >= SECP256K1_N) throw new Error('signature r out of range');
  const noble = new Uint8Array(65);
  noble[0] = v - 27;
  noble.set(r, 1);
  noble.set(s, 33);
  return { r, s, v, noble };
}

/** Recover the signer address of a 32-byte digest from an eth-layout signature. */
function recoverAddress(digest, sigHex) {
  const { noble } = parseEthSignature(sigHex);
  let pub;
  try {
    pub = secp.recoverPublicKey(noble, digest, { prehash: false });
  } catch (e) {
    throw new Error(`signature recovery failed: ${e.message}`);
  }
  const uncompressed = secp.Point.fromBytes(pub).toBytes(false);
  return toChecksumAddress(bytesToHex(keccak_256(uncompressed.slice(1)).slice(12)));
}

/** Sign a 32-byte digest; returns {r, s, yParity} (low-s by noble default). */
function signDigest(digest, privBytes) {
  const sig = secp.sign(digest, privBytes, { prehash: false, format: 'recovered' });
  const sigObj = secp.Signature.fromBytes(sig, 'recovered');
  return {
    r: bigintToMinimalBytes(sigObj.r),
    s: bigintToMinimalBytes(sigObj.s),
    yParity: sigObj.recovery,
    rWord: bigintToWord(sigObj.r),
    sWord: bigintToWord(sigObj.s),
    v: 27 + sigObj.recovery,
  };
}

/* ----------------------------------------------------------------- RLP -- */

function rlpEncode(item) {
  if (Array.isArray(item)) {
    const payload = Buffer.concat(item.map(rlpEncode));
    return Buffer.concat([rlpLength(payload.length, 0xc0), payload]);
  }
  const b = Buffer.from(item);
  if (b.length === 1 && b[0] < 0x80) return b;
  return Buffer.concat([rlpLength(b.length, 0x80), b]);
}

function rlpLength(len, offset) {
  if (len <= 55) return Buffer.from([offset + len]);
  let h = len.toString(16);
  if (h.length % 2) h = '0' + h;
  const lenBytes = Buffer.from(h, 'hex');
  return Buffer.concat([Buffer.from([offset + 55 + lenBytes.length]), lenBytes]);
}

/* -------------------------------------------------------------- EIP-1559 -- */

/**
 * Build and sign a type-2 transaction. All money/gas fields are BigInt.
 * Returns { rawTx (0x hex), hash (0x hex) }.
 */
function signEip1559Tx({ chainId, nonce, maxPriorityFeePerGas, maxFeePerGas, gasLimit, to, value = 0n, data }, privBytes) {
  const fields = [
    bigintToMinimalBytes(BigInt(chainId)),
    bigintToMinimalBytes(BigInt(nonce)),
    bigintToMinimalBytes(maxPriorityFeePerGas),
    bigintToMinimalBytes(maxFeePerGas),
    bigintToMinimalBytes(gasLimit),
    hexToBytes(to),
    bigintToMinimalBytes(value),
    hexToBytes(data),
    [], // accessList
  ];
  const sigHash = keccak_256(Buffer.concat([Buffer.from([0x02]), rlpEncode(fields)]));
  const { r, s, yParity } = signDigest(sigHash, privBytes);
  const signed = fields.concat([
    yParity === 0 ? new Uint8Array(0) : Uint8Array.from([1]),
    r, // already minimal big-endian (leading zeros stripped)
    s,
  ]);
  const rawBytes = Buffer.concat([Buffer.from([0x02]), rlpEncode(signed)]);
  return { rawTx: bytesToHex(rawBytes), hash: bytesToHex(keccak_256(rawBytes)) };
}

module.exports = {
  SECP256K1_N,
  SECP256K1_HALF_N,
  strip0x,
  isHex,
  hexToBytes,
  bytesToHex,
  bigintToMinimalBytes,
  bigintToWord,
  quantityToBigInt,
  keccak,
  toChecksumAddress,
  isAddress,
  validateAddress,
  addressEquals,
  privateKeyToAddress,
  domainSeparator,
  eip712Digest,
  parseEthSignature,
  recoverAddress,
  signDigest,
  rlpEncode,
  signEip1559Tx,
};
