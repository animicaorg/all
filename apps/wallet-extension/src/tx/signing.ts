/**
 * Canonical transaction signing.
 *
 * Mirrors the chain's two-stage signing layout (python/animica/tx/signing.py
 * + pq/py/sign.py:build_sign_bytes):
 *
 *   1. preimage = canonicalCBOR({1: domain, 2: chainId, 3: genesisHash,
 *                                4: network, 5: 'tx', 6: 1, 7: canonical_body})
 *   2. sign_bytes_raw = len(TAG)||TAG || len(DOMAIN)||DOMAIN
 *                       || len(CHAIN)||CHAIN || len(FORK)||FORK
 *                       || len(ALG)||ALG    || len(CONTEXT)||CONTEXT
 *                       || len(MSG)||MSG
 *      where MSG = preimage and CHAIN/FORK use uvarint (empty if null).
 *   3. sign_hash = SHA3-512(sign_bytes_raw)
 *   4. signature = pq_sign(sign_hash, secretKey, publicKey, algId)
 *
 * The node consumes sign_bytes_raw -> sha3_512 -> verifies the signature
 * against that 64-byte digest. Wallet preimage / sign_bytes must match the
 * node's bit-for-bit; this file is the single source of truth for that.
 */

import { sha3_512, sha3_256 } from 'js-sha3';
import { encodeCanonical, encodeTxBody, encodeTxEnvelope, toCanonicalBodyShape } from './encode';
import type { TxBody, ChainContext, TxEnvelope } from './types';
import { PREHASH_SHA3_512 } from './types';

const SIGN_TAG = 'animica:sign/v1';
const TX_SIGN_DOMAIN = 'animica.tx.v1';

function encodeUvarint(value: number | bigint): Uint8Array {
  let v = typeof value === 'bigint' ? value : BigInt(value);
  if (v < 0n) {
    throw new Error('uvarint requires non-negative integer');
  }
  const out: number[] = [];
  while (true) {
    const b = Number(v & 0x7fn);
    v >>= 7n;
    if (v > 0n) {
      out.push(b | 0x80);
    } else {
      out.push(b);
      break;
    }
  }
  return Uint8Array.from(out);
}

function lengthPrefixed(value: Uint8Array): Uint8Array {
  const lenBytes = encodeUvarint(value.length);
  const out = new Uint8Array(lenBytes.length + value.length);
  out.set(lenBytes, 0);
  out.set(value, lenBytes.length);
  return out;
}

function concatBytes(...chunks: Uint8Array[]): Uint8Array {
  let total = 0;
  for (const c of chunks) total += c.length;
  const out = new Uint8Array(total);
  let offset = 0;
  for (const c of chunks) {
    out.set(c, offset);
    offset += c.length;
  }
  return out;
}

/**
 * Build the canonical signing preimage. Matches Python tx_signing_preimage
 * with message_type="tx" and domain="animica.tx.v1".
 *
 * The body slot (key 7) holds the canonical V1 wire body — same dict the node
 * gets after normalize_tx_body. Object keys 1..7 are coerced to CBOR integers
 * by the encoder so the byte sequence matches Python's CBOR output.
 */
export function buildSigningPreimage(
  body: TxBody,
  context: ChainContext
): Uint8Array {
  const canonicalBody = toCanonicalBodyShape({
    chain_id: body.chain_id,
    nonce: body.nonce,
    from_addr: body.from_addr,
    to_addr: body.to_addr,
    value: body.value,
    fee: body.fee,
    gas_limit: body.gas_limit,
    data: body.data,
    kind: body.kind,
    code: body.code,
    manifest: body.manifest,
  });

  const preimage = {
    1: TX_SIGN_DOMAIN,
    2: context.chain_id,
    3: context.genesis_hash,
    4: context.network,
    5: 'tx',
    6: 1,
    7: canonicalBody,
  };

  return encodeCanonical(preimage);
}

/**
 * Build the canonical sign_bytes wrapper structure as the PQ backend expects
 * (matches pq/py/sign.py:build_sign_bytes for prehash="sha3-512").
 *
 * sign_bytes_raw layout (length-prefixed fields):
 *   len(TAG)||TAG
 *   len(DOMAIN)||DOMAIN
 *   len(CHAIN_ID_enc)||CHAIN_ID_enc   (CHAIN_ID_enc = uvarint(chain_id) or empty)
 *   len(FORK_ID_enc)||FORK_ID_enc     (FORK_ID_enc  = uvarint(fork_id)  or empty)
 *   len(ALG_ID_enc)||ALG_ID_enc
 *   len(CONTEXT)||CONTEXT             (CONTEXT = b"" for tx signing)
 *   len(MSG)||MSG
 */
function buildSignBytesRaw(opts: {
  msg: Uint8Array;
  domain: string;
  chainId: number | null;
  forkId: number | null;
  algId: number;
  context?: Uint8Array;
}): Uint8Array {
  const tag = new TextEncoder().encode(SIGN_TAG);
  const domainBytes = new TextEncoder().encode(opts.domain);
  const chainEnc = opts.chainId === null || opts.chainId === undefined
    ? new Uint8Array(0)
    : encodeUvarint(opts.chainId);
  const forkEnc = opts.forkId === null || opts.forkId === undefined
    ? new Uint8Array(0)
    : encodeUvarint(opts.forkId);
  const algEnc = encodeUvarint(opts.algId);
  const ctx = opts.context ?? new Uint8Array(0);

  return concatBytes(
    lengthPrefixed(tag),
    lengthPrefixed(domainBytes),
    lengthPrefixed(chainEnc),
    lengthPrefixed(forkEnc),
    lengthPrefixed(algEnc),
    lengthPrefixed(ctx),
    lengthPrefixed(opts.msg),
  );
}

/**
 * Compute the 64-byte digest that the PQ signer actually signs.
 *
 * This is *not* sha3_512(preimage) directly — that would mismatch the chain's
 * verify_detached() which always wraps the preimage with build_sign_bytes
 * before hashing. We replicate the wrapper exactly.
 */
export function computeSignHash(
  body: TxBody,
  context: ChainContext,
  algId: number = 0x1001,
): Uint8Array {
  const preimage = buildSigningPreimage(body, context);
  const signBytesRaw = buildSignBytesRaw({
    msg: preimage,
    domain: context.domain || 'tx',
    chainId: context.chain_id,
    forkId: context.fork_id ?? null,
    algId,
  });
  return new Uint8Array(sha3_512.array(signBytesRaw));
}

/**
 * Compute just the preimage bytes (for debugging).
 */
export function computeSignBytes(
  body: TxBody,
  context: ChainContext
): Uint8Array {
  return buildSigningPreimage(body, context);
}

/**
 * Compute transaction ID from envelope. txid = SHA3-256(canonical CBOR envelope).
 */
export function computeTxId(envelope: TxEnvelope): Uint8Array {
  const encoded = encodeTxEnvelope(envelope);
  return new Uint8Array(sha3_256.array(encoded));
}

/**
 * Sign a transaction body. The signer receives the wrapped 64-byte digest
 * (sha3_512 of build_sign_bytes_raw) so the resulting signature verifies on
 * the node side via verify_detached -> dilithium3.verify(pk, sign_bytes, sig).
 */
export async function signTxBody(
  body: TxBody,
  context: ChainContext,
  secretKey: Uint8Array,
  publicKey: Uint8Array,
  schemeId: number,
  signFunc: (
    message: Uint8Array,
    secretKey: Uint8Array,
    algId: number,
    publicKey?: Uint8Array,
  ) => Promise<Uint8Array>
): Promise<{
  scheme_id: number;
  pubkey_bytes: Uint8Array;
  signature_bytes: Uint8Array;
  prehash_id: number;
}> {
  const signHash = computeSignHash(body, context, schemeId);
  const signature = await signFunc(signHash, secretKey, schemeId, publicKey);
  return {
    scheme_id: schemeId,
    pubkey_bytes: publicKey,
    signature_bytes: signature,
    prehash_id: PREHASH_SHA3_512,
  };
}

export function fingerprint(data: Uint8Array): string {
  const hash = sha3_256.array(data);
  return '0x' + Array.from(hash.slice(0, 8))
    .map(b => b.toString(16).padStart(2, '0'))
    .join('');
}

export function bytesToHex(bytes: Uint8Array): string {
  return Array.from(bytes)
    .map(b => b.toString(16).padStart(2, '0'))
    .join('');
}

export function hexToBytes(hex: string): Uint8Array {
  const clean = hex.startsWith('0x') ? hex.slice(2) : hex;
  if (clean.length % 2 !== 0) {
    throw new Error('Hex string must have even length');
  }
  const bytes = new Uint8Array(clean.length / 2);
  for (let i = 0; i < bytes.length; i++) {
    bytes[i] = parseInt(clean.slice(i * 2, i * 2 + 2), 16);
  }
  return bytes;
}

export function buildDebugInfo(
  body: TxBody,
  auth: {
    scheme_id: number;
    pubkey_bytes: Uint8Array;
    signature_bytes: Uint8Array;
    prehash_id: number;
  },
  context: ChainContext,
  txid: Uint8Array,
  rawTx: Uint8Array
): {
  body_cbor_hex: string;
  preimage_hex: string;
  sign_hash_hex: string;
  pubkey_hex: string;
  pubkey_fingerprint: string;
  signature_hex: string;
  signature_fingerprint: string;
  scheme_id: number;
  prehash_id: number;
  chain_id: number;
  genesis_hash_hex: string;
  network: string;
  domain: string;
  txid_hex: string;
  raw_tx_hex: string;
} {
  const bodyCbor = encodeTxBody(body);
  const preimage = buildSigningPreimage(body, context);
  const signHash = computeSignHash(body, context, auth.scheme_id);

  return {
    body_cbor_hex: '0x' + bytesToHex(bodyCbor),
    preimage_hex: '0x' + bytesToHex(preimage),
    sign_hash_hex: '0x' + bytesToHex(signHash),
    pubkey_hex: '0x' + bytesToHex(auth.pubkey_bytes),
    pubkey_fingerprint: fingerprint(auth.pubkey_bytes),
    signature_hex: '0x' + bytesToHex(auth.signature_bytes),
    signature_fingerprint: fingerprint(auth.signature_bytes),
    scheme_id: auth.scheme_id,
    prehash_id: auth.prehash_id,
    chain_id: context.chain_id,
    genesis_hash_hex: '0x' + bytesToHex(context.genesis_hash),
    network: context.network,
    domain: context.domain,
    txid_hex: '0x' + bytesToHex(txid),
    raw_tx_hex: '0x' + bytesToHex(rawTx),
  };
}
