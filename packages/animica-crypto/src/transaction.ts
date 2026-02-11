import { buildSignBytes } from './signing.js';

/**
 * Transaction structure (simplified)
 */
export interface Transaction {
  from?: string;
  to?: string;
  value?: string | number | bigint;
  nonce?: number;
  gasLimit?: any;
  data?: string | Uint8Array;
  [key: string]: any;
}

/**
 * Options for building transaction SignBytes
 */
export interface TxSignBytesOptions {
  /** Transaction object */
  tx: Transaction;
  /** Chain ID */
  chainId: number;
  /** Domain (default: "animica.tx.v1") */
  domain?: string;
  /** Genesis hash (optional) */
  genesisHash?: Uint8Array;
  /** Network name (optional) */
  network?: string;
  /** Fork ID (optional) */
  forkId?: number;
}

/**
 * Build transaction SignBytes
 * 
 * This matches the Python implementation in python/animica/tx/signing.py
 * The actual implementation requires CBOR encoding which is complex.
 * 
 * For now, this is a PLACEHOLDER that shows the structure.
 * The full implementation should encode the transaction as CBOR following
 * the exact format used by the node.
 */
export function txSignBytes(options: TxSignBytesOptions): Uint8Array {
  throw new Error(
    'Transaction SignBytes builder not yet fully implemented. ' +
    'This requires:\n' +
    '1. CBOR encoding library compatible with Python cbor2\n' +
    '2. Exact transaction normalization rules from node\n' +
    '3. Canonical field ordering\n' +
    '\n' +
    'For signing transactions, use the wallet extension which will build ' +
    'SignBytes internally and present it for user approval.\n' +
    '\n' +
    'For verification/diagnostics, the Dapp IDE can request the SignBytes ' +
    'hash from the node after transaction submission.'
  );
  
  // Placeholder structure (NOT FUNCTIONAL):
  /*
  const {
    tx,
    chainId,
    domain = 'animica.tx.v1',
    genesisHash = new Uint8Array(0),
    network = 'unknown',
    forkId
  } = options;
  
  // Build CBOR preimage:
  // {
  //   1: domain,
  //   2: chainId,
  //   3: genesisHash,
  //   4: network,
  //   5: "tx",
  //   6: 1,  // tx_version
  //   7: normalizedTxBody
  // }
  
  // This would need a CBOR library and exact normalization rules
  const cborPreimage = encodeCBOR({
    1: domain,
    2: chainId,
    3: genesisHash,
    4: network,
    5: 'tx',
    6: 1,
    7: normalizeTxBody(tx)
  });
  
  // Then use SHA3-512
  return sha3_512(cborPreimage);
  */
}
