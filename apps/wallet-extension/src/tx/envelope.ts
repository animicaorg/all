/**
 * Transaction envelope building
 * 
 * Creates complete TxEnvelope structures ready for RPC submission.
 */

import type { TxBody, TxAuth, TxEnvelope, ChainContext, TxBuildParams, SignedTxResult } from './types';
import { DOMAIN_TX_SIGN, PREHASH_SHA3_512, getSchemeInfo } from './types';
import { signTxBody, computeTxId, bytesToHex } from './signing';
import { encodeTxEnvelope } from './encode';

/**
 * Derive address from public key
 * 
 * TODO: This should use the same derivation as the node (pq/py/address.py)
 * For now, this is a placeholder that returns empty string.
 * Real implementation should use Blake2b hash + bech32m encoding.
 */
export function deriveAddress(publicKey: Uint8Array, schemeId: number): string {
  // TODO: Implement proper address derivation
  // This is a critical guardrail that prevents signing with wrong keys
  console.warn('deriveAddress not yet implemented - address validation skipped');
  return '';
}

/**
 * Validate that address matches public key
 * 
 * This is a critical guardrail to prevent signing with the wrong key.
 */
export function validateAddressBinding(
  fromAddr: string,
  publicKey: Uint8Array,
  schemeId: number
): void {
  const derived = deriveAddress(publicKey, schemeId);
  if (derived && derived !== fromAddr) {
    throw new Error(
      `Address/pubkey mismatch: from=${fromAddr}, derived=${derived}. ` +
      `Refusing to sign with mismatched key.`
    );
  }
}

/**
 * Validate scheme and key/signature lengths
 */
export function validateScheme(
  schemeId: number,
  publicKey: Uint8Array,
  signature?: Uint8Array
): void {
  const schemeInfo = getSchemeInfo(schemeId);
  if (!schemeInfo) {
    throw new Error(`Unsupported scheme_id: ${schemeId}`);
  }
  
  if (publicKey.length !== schemeInfo.pubkey) {
    throw new Error(
      `Invalid pubkey length for ${schemeInfo.name}: ` +
      `expected ${schemeInfo.pubkey}, got ${publicKey.length}`
    );
  }
  
  if (signature && signature.length !== schemeInfo.signature) {
    throw new Error(
      `Invalid signature length for ${schemeInfo.name}: ` +
      `expected ${schemeInfo.signature}, got ${signature.length}`
    );
  }
}

/**
 * Convert bech32 address to raw bytes
 * 
 * TODO: Implement proper bech32m decoding
 * For now, this is a placeholder.
 */
export function addressToBytes(address: string): Uint8Array {
  // TODO: Implement bech32m decode
  // This should decode the bech32m address to raw bytes
  console.warn('addressToBytes not yet fully implemented');
  
  // Placeholder: return empty bytes for now
  // Real implementation should decode bech32m format
  return new Uint8Array(32); // Placeholder
}

/**
 * Build and sign a transaction
 * 
 * This is the main entry point for creating a signed transaction.
 * 
 * @param params - Transaction parameters
 * @param context - Chain context (must include genesis_hash, network)
 * @param secretKey - Secret key bytes
 * @param publicKey - Public key bytes
 * @param schemeId - Signature scheme ID
 * @param signFunc - PQ signing function
 * @returns Signed transaction ready for submission
 */
export async function buildAndSignTransaction(
  params: TxBuildParams,
  context: ChainContext,
  secretKey: Uint8Array,
  publicKey: Uint8Array,
  schemeId: number,
  signFunc: (message: Uint8Array, secretKey: Uint8Array, algId: number) => Promise<Uint8Array>
): Promise<SignedTxResult> {
  // Validate inputs
  if (!secretKey || secretKey.length === 0) {
    throw new Error('secretKey is required');
  }
  if (!publicKey || publicKey.length === 0) {
    throw new Error('publicKey is required');
  }
  if (!params.from || typeof params.from !== 'string') {
    throw new Error('from address is required');
  }
  if (!params.to || typeof params.to !== 'string') {
    throw new Error('to address is required');
  }
  if (!context.genesis_hash || context.genesis_hash.length !== 32) {
    throw new Error('context.genesis_hash is required (32 bytes)');
  }
  if (!context.network) {
    throw new Error('context.network is required');
  }
  
  // Validate scheme and key lengths
  validateScheme(schemeId, publicKey);
  
  // Validate address/pubkey binding
  // (This will be a no-op until deriveAddress is implemented)
  validateAddressBinding(params.from, publicKey, schemeId);
  
  // Build transaction body
  const body: TxBody = {
    version: 1,
    chain_id: context.chain_id,
    nonce: params.nonce,
    from_addr: addressToBytes(params.from),
    to_addr: addressToBytes(params.to),
    value: params.value,
    fee: params.fee,
    gas_limit: params.gas_limit,
    data: params.data || new Uint8Array(),
    memo: params.memo || '',
    timestamp: params.timestamp || Math.floor(Date.now() / 1000),
    kind: 0, // 0 = transfer
  };
  
  // Sign the transaction
  const auth = await signTxBody(
    body,
    context,
    secretKey,
    publicKey,
    schemeId,
    signFunc
  );
  
  // Validate signature length
  validateScheme(schemeId, publicKey, auth.signature_bytes);
  
  // Create envelope with placeholder txid
  const envelope: TxEnvelope = {
    body,
    auth,
    txid: new Uint8Array(32),
  };
  
  // Compute real txid
  const txid = computeTxId(envelope);
  envelope.txid = txid;
  
  // Encode for RPC
  const rawTxBytes = encodeTxEnvelope(envelope);
  const rawTx = '0x' + bytesToHex(rawTxBytes);
  
  return {
    envelope,
    txid: bytesToHex(txid),
    rawTx,
  };
}

/**
 * Create a default chain context (for development)
 * 
 * Production code MUST fetch this from the RPC node via chain.getChainIdentity.
 */
export function createDefaultContext(chainId: number): ChainContext {
  console.warn('Using default chain context - production code must fetch from RPC');
  return {
    chain_id: chainId,
    genesis_hash: new Uint8Array(32), // Placeholder
    network: 'unknown',
    fork_id: null,
    domain: DOMAIN_TX_SIGN,
    prehash: 'sha3-512',
  };
}
