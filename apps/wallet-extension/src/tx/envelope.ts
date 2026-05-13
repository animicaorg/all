/**
 * Transaction envelope building
 * 
 * Creates complete TxEnvelope structures ready for RPC submission.
 */

import type { TxBody, TxAuth, TxEnvelope, ChainContext, TxBuildParams, SignedTxResult } from './types';
import { DOMAIN_TX_SIGN, PREHASH_SHA3_512, getSchemeInfo, algIdToSchemeId } from './types';
import { signTxBody, computeTxId, bytesToHex } from './signing';
import { encodeTxEnvelope } from './encode';
import { addressFromPubkey, addressToBytes as coreAddressToBytes } from '../core/crypto/address';

/**
 * Derive address from public key using canonical algorithm
 * 
 * This matches the node's pq/py/address.py implementation:
 * - address = bech32m(hrp="anim", alg_id(2 bytes) || sha3_256(pubkey))
 */
export function deriveAddress(publicKey: Uint8Array, algId: number): string {
  return addressFromPubkey(publicKey, algId);
}

/**
 * Validate that address matches public key
 * 
 * This is a critical guardrail to prevent signing with the wrong key.
 */
export function validateAddressBinding(
  fromAddr: string,
  publicKey: Uint8Array,
  algId: number
): void {
  const derived = deriveAddress(publicKey, algId);
  if (derived !== fromAddr) {
    throw new Error(
      `Address/pubkey mismatch: from=${fromAddr}, derived=${derived}. ` +
      `Refusing to sign with mismatched key. ` +
      `This indicates the wallet is trying to sign with the wrong private key.`
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
 * Convert bech32 address to raw bytes (digest only)
 * 
 * This decodes the bech32m address to the 32-byte digest.
 */
export function addressToBytes(address: string): Uint8Array {
  return coreAddressToBytes(address);
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
 * @param algId - Algorithm ID (4097=Dilithium3, 4098=SPHINCS+)
 * @param signFunc - PQ signing function
 * @returns Signed transaction ready for submission
 */
export async function buildAndSignTransaction(
  params: TxBuildParams,
  context: ChainContext,
  secretKey: Uint8Array,
  publicKey: Uint8Array,
  algId: number,
  signFunc: (
    message: Uint8Array,
    secretKey: Uint8Array,
    algId: number,
    publicKey?: Uint8Array,
  ) => Promise<Uint8Array>
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
  if (!context.genesis_hash || context.genesis_hash.length !== 32) {
    throw new Error('context.genesis_hash is required (32 bytes)');
  }
  if (!context.network) {
    throw new Error('context.network is required');
  }

  // Pick the tx kind. Explicit override wins; otherwise infer:
  //   deploy if code/manifest provided,
  //   call if data provided,
  //   transfer otherwise.
  const hasDeployPayload = !!(params.code && params.code.length > 0)
    || !!(params.manifest && params.manifest.length > 0);
  const hasCallData = !!(params.data && params.data.length > 0);
  let kind: number;
  if (typeof params.kind === 'number') {
    kind = params.kind;
  } else if (hasDeployPayload) {
    kind = 1;
  } else if (hasCallData) {
    kind = 2;
  } else {
    kind = 0;
  }

  // Validate per-kind requirements
  if (kind === 1) {
    if (!params.code || params.code.length === 0) {
      throw new Error('deploy tx requires code bytes');
    }
    if (!params.manifest || params.manifest.length === 0) {
      throw new Error('deploy tx requires manifest bytes');
    }
  } else {
    if (!params.to || typeof params.to !== 'string') {
      throw new Error('to address is required for transfer/call');
    }
  }

  // Convert algId to schemeId
  const schemeId = algIdToSchemeId(algId);
  if (schemeId === null) {
    throw new Error(`Unsupported algorithm ID: 0x${algId.toString(16)}`);
  }

  // Validate scheme and key lengths
  validateScheme(schemeId, publicKey);

  // CRITICAL: Validate address/pubkey binding
  validateAddressBinding(params.from, publicKey, algId);

  // Build transaction body. Deploy txs have no `to` — we still include a
  // zeroed 32-byte buffer in the internal representation; toCanonicalBodyShape
  // ignores it for kind=1 and emits the {code,manifest} payload instead.
  const payloadData = params.data || new Uint8Array();
  const toAddrBytes = params.to ? addressToBytes(params.to) : new Uint8Array(32);
  const body: TxBody = {
    version: 1,
    chain_id: context.chain_id,
    nonce: params.nonce,
    from_addr: addressToBytes(params.from),
    to_addr: toAddrBytes,
    value: params.value,
    fee: params.fee,
    gas_limit: params.gas_limit,
    data: payloadData,
    memo: params.memo || '',
    timestamp: params.timestamp || Math.floor(Date.now() / 1000),
    kind,
    code: params.code,
    manifest: params.manifest,
  };
  
  // Sign the transaction. We pass the chain's alg_id (4097/4098) — not the
  // wallet-internal scheme_id (1/2) — because both the canonical sign_bytes
  // wrapper and the wire envelope's sigs[].alg field key off alg_id.
  const auth = await signTxBody(
    body,
    context,
    secretKey,
    publicKey,
    algId,
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
