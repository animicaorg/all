'use strict';
/**
 * Just enough Solana to self-facilitate an SPL TransferChecked: base58,
 * compact-u16 ("shortvec"), transaction wire parsing (legacy + v0), ed25519
 * via node:crypto, SPL token account layout, and a JSON-RPC client over
 * global fetch. Zero dependencies by house rule (see animica-pay): a payments
 * path's attack surface should be code we have read.
 *
 * Program ids below are verified against the wANM bridge's own sources
 * (bridge/services/bridge-worker/src/pollVault.ts and its vendored
 * @solana/spl-* packages), not from memory.
 */

const crypto = require('node:crypto');

const TOKEN_PROGRAM_ID = 'TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA';
const TOKEN_2022_PROGRAM_ID = 'TokenzQdBNbLqP5VEhdkAS6EPFLC1PHnBqCXEpPxuEb';
const MEMO_PROGRAM_ID = 'MemoSq4gqABAXKb96qnH8TysNcWxMyWCqXgDLGmfcHr';
const COMPUTE_BUDGET_PROGRAM_ID = 'ComputeBudget111111111111111111111111111111';
// Wallet-injected guard assertions (Phantom injects up to 3, Solflare 2);
// spec scheme_exact_svm.md Path 1 says these MUST be allowed. Id from the spec.
const LIGHTHOUSE_PROGRAM_ID = 'L2TExMFKdjpN9kozasaurPirfHy9P8sbXoAN1qA3S95';

/* --------------------------------------------------------------- base58 -- */

const B58_ALPHABET = '123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz';
const B58_MAP = new Map([...B58_ALPHABET].map((c, i) => [c, BigInt(i)]));

function base58Encode(buf) {
  let zeros = 0;
  while (zeros < buf.length && buf[zeros] === 0) zeros++;
  let n = BigInt('0x' + (buf.toString('hex') || '0'));
  let out = '';
  while (n > 0n) {
    out = B58_ALPHABET[Number(n % 58n)] + out;
    n /= 58n;
  }
  return '1'.repeat(zeros) + out;
}

function base58Decode(str) {
  if (typeof str !== 'string' || str.length === 0) throw new Error('empty base58');
  let zeros = 0;
  while (zeros < str.length && str[zeros] === '1') zeros++;
  let n = 0n;
  for (const c of str) {
    const v = B58_MAP.get(c);
    if (v === undefined) throw new Error(`invalid base58 char: ${c}`);
    n = n * 58n + v;
  }
  let hex = n.toString(16);
  if (hex.length % 2) hex = '0' + hex;
  const body = n === 0n ? Buffer.alloc(0) : Buffer.from(hex, 'hex');
  return Buffer.concat([Buffer.alloc(zeros), body]);
}

/** Decode a base58 pubkey and insist on 32 bytes; anything else is not a key. */
function decodePubkey(str) {
  const b = base58Decode(str);
  if (b.length !== 32) throw new Error(`pubkey ${str} decodes to ${b.length} bytes, want 32`);
  return b;
}

/* --------------------------------------------- compact-u16 ("shortvec") -- */

function shortvecEncode(n) {
  if (n < 0 || n > 0xffff) throw new Error(`shortvec out of range: ${n}`);
  const out = [];
  let v = n;
  for (;;) {
    const b = v & 0x7f;
    v >>= 7;
    if (v === 0) { out.push(b); break; }
    out.push(b | 0x80);
  }
  return Buffer.from(out);
}

function shortvecDecode(buf, offset) {
  let n = 0;
  let shift = 0;
  let i = offset;
  for (;;) {
    if (i >= buf.length) throw new Error('shortvec ran off the end');
    const b = buf[i++];
    n |= (b & 0x7f) << shift;
    if ((b & 0x80) === 0) break;
    shift += 7;
    if (shift > 14) throw new Error('shortvec too long');
  }
  return [n, i];
}

/* ---------------------------------------------------------- transaction -- */

/**
 * Parse a base64 (possibly partially-signed) transaction into signatures +
 * message. Supports legacy and v0 messages. v0 address-table lookups are
 * parsed but the caller is expected to REJECT transactions that use them: the
 * static verification below cannot resolve looked-up addresses without extra
 * RPC round-trips, and the exact-svm scheme has no need for them.
 */
function parseTransaction(base64) {
  const raw = Buffer.from(base64, 'base64');
  if (raw.length === 0 || raw.length > 1644) {
    // Wire limit is 1232; base64-decoded anything much larger is garbage.
    throw new Error(`transaction size ${raw.length} out of range`);
  }
  let [numSigs, off] = shortvecDecode(raw, 0);
  if (numSigs === 0 || numSigs > 12) throw new Error(`implausible signature count ${numSigs}`);
  const signatures = [];
  for (let i = 0; i < numSigs; i++) {
    if (off + 64 > raw.length) throw new Error('signature section truncated');
    signatures.push(raw.subarray(off, off + 64));
    off += 64;
  }
  const messageBytes = raw.subarray(off);
  if (messageBytes.length < 3 + 1 + 32 + 32) throw new Error('message truncated');

  let mOff = 0;
  let version = null; // null = legacy
  if (messageBytes[0] & 0x80) {
    version = messageBytes[0] & 0x7f;
    mOff = 1;
  }
  const header = {
    numRequiredSignatures: messageBytes[mOff],
    numReadonlySigned: messageBytes[mOff + 1],
    numReadonlyUnsigned: messageBytes[mOff + 2],
  };
  mOff += 3;
  let numKeys;
  [numKeys, mOff] = shortvecDecode(messageBytes, mOff);
  if (numKeys === 0 || numKeys > 64) throw new Error(`implausible key count ${numKeys}`);
  const accountKeys = [];
  for (let i = 0; i < numKeys; i++) {
    if (mOff + 32 > messageBytes.length) throw new Error('account keys truncated');
    accountKeys.push(base58Encode(messageBytes.subarray(mOff, mOff + 32)));
    mOff += 32;
  }
  if (mOff + 32 > messageBytes.length) throw new Error('blockhash truncated');
  const recentBlockhash = base58Encode(messageBytes.subarray(mOff, mOff + 32));
  mOff += 32;

  let numIx;
  [numIx, mOff] = shortvecDecode(messageBytes, mOff);
  if (numIx > 16) throw new Error(`implausible instruction count ${numIx}`);
  const instructions = [];
  for (let i = 0; i < numIx; i++) {
    const programIdIndex = messageBytes[mOff];
    mOff += 1;
    let nAcc;
    [nAcc, mOff] = shortvecDecode(messageBytes, mOff);
    if (mOff + nAcc > messageBytes.length) throw new Error('instruction accounts truncated');
    const accounts = [...messageBytes.subarray(mOff, mOff + nAcc)];
    mOff += nAcc;
    let dLen;
    [dLen, mOff] = shortvecDecode(messageBytes, mOff);
    if (mOff + dLen > messageBytes.length) throw new Error('instruction data truncated');
    const data = messageBytes.subarray(mOff, mOff + dLen);
    mOff += dLen;
    instructions.push({ programIdIndex, accounts, data });
  }

  let addressTableLookupCount = 0;
  if (version !== null) {
    [addressTableLookupCount, mOff] = shortvecDecode(messageBytes, mOff);
    // We do not resolve lookups; count alone is enough to reject.
  }

  return {
    signatures,
    version,
    header,
    accountKeys,
    recentBlockhash,
    instructions,
    addressTableLookupCount,
    messageBytes, // exactly what every signer signs (prefix byte included for v0)
  };
}

/** Reassemble signatures + message into base64 wire form. */
function serializeTransaction(signatures, messageBytes) {
  return Buffer.concat([shortvecEncode(signatures.length), ...signatures, messageBytes])
    .toString('base64');
}

/* -------------------------------------------------------------- ed25519 -- */

// DER scaffolding so node:crypto accepts raw 32-byte ed25519 material.
const PKCS8_PREFIX = Buffer.from('302e020100300506032b657004220420', 'hex');
const SPKI_PREFIX = Buffer.from('302a300506032b6570032100', 'hex');

/** 32-byte seed -> { publicKey (base58), publicKeyBytes, sign(msg) }. */
function keypairFromSeed(seed) {
  if (!Buffer.isBuffer(seed) || seed.length !== 32) throw new Error('seed must be 32 bytes');
  const privateKey = crypto.createPrivateKey({
    key: Buffer.concat([PKCS8_PREFIX, seed]),
    format: 'der',
    type: 'pkcs8',
  });
  const spki = crypto.createPublicKey(privateKey).export({ format: 'der', type: 'spki' });
  const publicKeyBytes = Buffer.from(spki.subarray(spki.length - 32));
  return {
    publicKey: base58Encode(publicKeyBytes),
    publicKeyBytes,
    sign: (msg) => crypto.sign(null, msg, privateKey),
  };
}

function randomKeypair() {
  return keypairFromSeed(crypto.randomBytes(32));
}

/** Accepts hex (64 chars) or base58 for a 32-byte seed. */
function seedFromString(s) {
  if (/^[0-9a-fA-F]{64}$/.test(s)) return Buffer.from(s, 'hex');
  const b = base58Decode(s);
  if (b.length === 64) return Buffer.from(b.subarray(0, 32)); // solana "secret key" = seed||pub
  if (b.length !== 32) throw new Error('seed must decode to 32 (or 64) bytes');
  return Buffer.from(b);
}

function verifySignature(messageBytes, signature, pubkeyBase58) {
  const key = crypto.createPublicKey({
    key: Buffer.concat([SPKI_PREFIX, decodePubkey(pubkeyBase58)]),
    format: 'der',
    type: 'spki',
  });
  return crypto.verify(null, messageBytes, key, signature);
}

const ZERO_SIG = Buffer.alloc(64);
function isPlaceholderSignature(sig) {
  return sig.equals(ZERO_SIG);
}

/* ----------------------------------------------------- SPL token layout -- */

/**
 * SPL token account (165 bytes): mint[0..32], owner[32..64], amount u64 LE
 * [64..72]. Token-2022 accounts are >=165 with extensions appended; the base
 * layout is identical, so parsing the first 72 bytes is valid for both.
 */
function parseTokenAccount(dataB64) {
  const data = Buffer.from(dataB64, 'base64');
  if (data.length < 72) throw new Error(`token account data too short: ${data.length}`);
  return {
    mint: base58Encode(data.subarray(0, 32)),
    owner: base58Encode(data.subarray(32, 64)),
    amount: data.readBigUInt64LE(64),
  };
}

/* ------------------------------------------------------------- JSON-RPC -- */

/**
 * Minimal Solana JSON-RPC client. `call(method, params)` returns the parsed
 * `result`. Injectable everywhere it is used so tests never touch a network.
 */
function rpcClient(url, { fetchImpl = fetch, timeoutMs = 10_000 } = {}) {
  if (!url) throw new Error('SOLANA_RPC_URL is not configured');
  let nextId = 1;
  return {
    async call(method, params) {
      const res = await fetchImpl(url, {
        method: 'POST',
        headers: { 'content-type': 'application/json' },
        body: JSON.stringify({ jsonrpc: '2.0', id: nextId++, method, params }),
        signal: AbortSignal.timeout(timeoutMs),
      });
      if (!res.ok) throw new Error(`solana rpc http ${res.status}`);
      const body = await res.json();
      if (body.error) throw new Error(`solana rpc ${method}: ${body.error.message || body.error.code}`);
      return body.result;
    },
  };
}

module.exports = {
  TOKEN_PROGRAM_ID,
  TOKEN_2022_PROGRAM_ID,
  MEMO_PROGRAM_ID,
  COMPUTE_BUDGET_PROGRAM_ID,
  LIGHTHOUSE_PROGRAM_ID,
  base58Encode,
  base58Decode,
  decodePubkey,
  shortvecEncode,
  shortvecDecode,
  parseTransaction,
  serializeTransaction,
  keypairFromSeed,
  randomKeypair,
  seedFromString,
  verifySignature,
  isPlaceholderSignature,
  parseTokenAccount,
  rpcClient,
};
