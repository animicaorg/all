/**
 * Canonical transaction signing
 * 
 * This module implements the exact signing process used by the node.
 * See SIGNING_SPEC.md for detailed specification.
 */

import { sha3_512, sha3_256 } from 'js-sha3';
import { encodeCanonical, encodeTxBody, encodeTxEnvelope } from './encode';
import type { TxBody, ChainContext, TxEnvelope } from './types';
import { DOMAIN_TX_SIGN, PREHASH_SHA3_512 } from './types';

/**
 * Build the canonical signing preimage for a transaction
 * 
 * The preimage structure (matching animica/tx/signing.py):
 * {
 *   1: domain ("animica.tx.v1"),
 *   2: chain_id,
 *   3: genesis_hash,
 *   4: network,
 *   5: message_type ("tx"),
 *   6: version,
 *   7: body
 * }
 */
export function buildSigningPreimage(
  body: TxBody,
  context: ChainContext
): Uint8Array {
  const preimage = {
    1: context.domain,
    2: context.chain_id,
    3: context.genesis_hash,
    4: context.network,
    5: 'tx', // message_type
    6: body.version,
    7: {
      version: body.version,
      chain_id: body.chain_id,
      nonce: body.nonce,
      from_addr: body.from_addr,
      to_addr: body.to_addr,
      value: body.value,
      fee: body.fee,
      gas_limit: body.gas_limit,
      data: body.data,
      memo: body.memo,
      timestamp: body.timestamp,
      kind: body.kind,
    },
  };
  
  return encodeCanonical(preimage);
}

/**
 * Compute the sign hash (SHA3-512 of preimage)
 * 
 * This is the 64-byte digest that gets signed by the PQ algorithm.
 */
export function computeSignHash(
  body: TxBody,
  context: ChainContext
): Uint8Array {
  const preimage = buildSigningPreimage(body, context);
  return new Uint8Array(sha3_512.array(preimage));
}

/**
 * Compute just the preimage bytes (for debugging)
 */
export function computeSignBytes(
  body: TxBody,
  context: ChainContext
): Uint8Array {
  return buildSigningPreimage(body, context);
}

/**
 * Compute transaction ID from envelope
 * 
 * TxID = SHA3-256(canonical CBOR encoding of envelope)
 */
export function computeTxId(envelope: TxEnvelope): Uint8Array {
  const encoded = encodeTxEnvelope(envelope);
  return new Uint8Array(sha3_256.array(encoded));
}

/**
 * Sign a transaction body to create an auth structure
 * 
 * @param body - Transaction body to sign
 * @param context - Chain context (chain_id, genesis_hash, network, etc.)
 * @param secretKey - Secret key bytes
 * @param publicKey - Public key bytes
 * @param schemeId - Signature scheme (1=Dilithium3, 2=SPHINCS+)
 * @param signFunc - PQ signing function
 * @returns Auth structure with signature
 */
export async function signTxBody(
  body: TxBody,
  context: ChainContext,
  secretKey: Uint8Array,
  publicKey: Uint8Array,
  schemeId: number,
  signFunc: (message: Uint8Array, secretKey: Uint8Array, algId: number) => Promise<Uint8Array>
): Promise<{
  scheme_id: number;
  pubkey_bytes: Uint8Array;
  signature_bytes: Uint8Array;
  prehash_id: number;
}> {
  // Compute sign hash
  const signHash = computeSignHash(body, context);
  
  // Sign the hash (NOT the hex string, NOT JSON, RAW BYTES)
  const signature = await signFunc(signHash, secretKey, schemeId);
  
  return {
    scheme_id: schemeId,
    pubkey_bytes: publicKey,
    signature_bytes: signature,
    prehash_id: PREHASH_SHA3_512,
  };
}

/**
 * Compute a fingerprint of bytes for logging/debugging
 */
export function fingerprint(data: Uint8Array): string {
  const hash = sha3_256.array(data);
  return '0x' + Array.from(hash.slice(0, 8))
    .map(b => b.toString(16).padStart(2, '0'))
    .join('');
}

/**
 * Convert bytes to hex string
 */
export function bytesToHex(bytes: Uint8Array): string {
  return Array.from(bytes)
    .map(b => b.toString(16).padStart(2, '0'))
    .join('');
}

/**
 * Convert hex string to bytes
 */
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

/**
 * Build a debug info bundle for a signed transaction
 */
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
  const signHash = computeSignHash(body, context);
  
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
