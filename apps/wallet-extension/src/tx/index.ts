/**
 * Canonical transaction signing and submission
 * 
 * This module provides node-grade transaction building, signing, and submission
 * that matches the Animica node's canonical implementation exactly.
 * 
 * Usage:
 * 
 * ```typescript
 * import { buildAndSignTransaction, fetchChainContext, submitTransaction } from './tx';
 * import { sign } from '../core/crypto/pq';
 * 
 * // 1. Fetch chain context (REQUIRED)
 * const context = await fetchChainContext(rpcClient.call.bind(rpcClient));
 * 
 * // 2. Build and sign transaction
 * const result = await buildAndSignTransaction(
 *   {
 *     from: 'anim1...',
 *     to: 'anim1...',
 *     value: 1000,
 *     fee: 1000000000,
 *     gas_limit: 21000,
 *     nonce: 0,
 *   },
 *   context,
 *   secretKey,
 *   publicKey,
 *   schemeId,
 *   sign
 * );
 * 
 * // 3. Submit to node
 * const txHash = await submitTransaction(result.rawTx, rpcClient.call.bind(rpcClient));
 * ```
 */

// Types
export type {
  TxBody,
  TxAuth,
  TxEnvelope,
  ChainContext,
  TxBuildParams,
  SignedTxResult,
  TxDebugInfo,
} from './types';

export {
  SCHEME_DILITHIUM3,
  SCHEME_SPHINCS_SHAKE_128S,
  ALG_ID_DILITHIUM3,
  ALG_ID_SPHINCS,
  PREHASH_NONE,
  PREHASH_SHA3_256,
  PREHASH_SHA3_512,
  DOMAIN_TX_SIGN,
  KEY_LENGTHS,
  getSchemeInfo,
  algIdToSchemeId,
  schemeIdToAlgId,
} from './types';

// Encoding
export {
  encodeCanonical,
  encodeTxBody,
  encodeTxAuth,
  encodeTxEnvelope,
} from './encode';

// Signing
export {
  buildSigningPreimage,
  computeSignHash,
  computeSignBytes,
  computeTxId,
  signTxBody,
  fingerprint,
  bytesToHex,
  hexToBytes,
  buildDebugInfo,
} from './signing';

// Envelope building
export {
  buildAndSignTransaction,
  deriveAddress,
  validateAddressBinding,
  validateScheme,
  addressToBytes,
  createDefaultContext,
} from './envelope';

// RPC
export {
  fetchChainContext,
  submitTransaction,
  parseRpcError,
  isSignatureError,
} from './rpc';
