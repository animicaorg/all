/**
 * Canonical transaction types matching the node's coretx module
 */

export interface TxBody {
  version: number;
  chain_id: number;
  nonce: number;
  from_addr: Uint8Array;
  to_addr: Uint8Array;
  value: bigint | number;
  fee: bigint | number;
  gas_limit: bigint | number;
  data: Uint8Array;
  memo: string;
  timestamp: number;
  kind: number;            // 0=transfer, 1=deploy, 2=call, 3=transfer-with-data
  code?: Uint8Array;       // kind=1 deploy payload
  manifest?: Uint8Array;   // kind=1 deploy payload
}

export interface TxAuth {
  scheme_id: number;      // 1 = Dilithium3, 2 = SPHINCS+
  pubkey_bytes: Uint8Array;
  signature_bytes: Uint8Array;
  prehash_id: number;     // 2 = SHA3-512
}

export interface TxEnvelope {
  body: TxBody;
  auth: TxAuth;
  txid: Uint8Array;       // 32 bytes
}

export interface ChainContext {
  chain_id: number;
  genesis_hash: Uint8Array;  // 32 bytes
  network: string;
  fork_id: number | null;
  domain: string;            // "animica.tx.v1"
  prehash: string;           // "sha3-512"
}

export interface SigningPreimage {
  domain: string;
  chain_id: number;
  genesis_hash: Uint8Array;
  network: string;
  message_type: string;
  version: number;
  body: TxBody;
}

export interface TxBuildParams {
  from: string;                     // bech32 address (sender)
  to?: string;                      // bech32 address; required for transfer/call, omitted for deploy
  value: bigint | number;
  fee: bigint | number;
  gas_limit: bigint | number;
  nonce: number;
  data?: Uint8Array;
  memo?: string;
  timestamp?: number;
  /**
   * Explicit kind override.
   *   0=transfer, 1=deploy (contract create), 2=call (contract call with data)
   * If omitted: deploy when {code,manifest} present, call when data present, else transfer.
   */
  kind?: number;
  code?: Uint8Array;                // kind=1 deploy: contract bytecode
  manifest?: Uint8Array;            // kind=1 deploy: contract manifest JSON bytes
}

export interface SignedTxResult {
  envelope: TxEnvelope;
  txid: string;       // hex
  rawTx: string;      // 0x-prefixed hex
}

export interface TxDebugInfo {
  body: TxBody;
  body_cbor_hex: string;
  preimage_hex: string;
  sign_hash_hex: string;
  pubkey_hex: string;
  pubkey_fingerprint: string;
  scheme_id: number;
  scheme_name: string;
  signature_hex: string;
  signature_fingerprint: string;
  chain_id: number;
  genesis_hash_hex: string;
  network: string;
  domain: string;
  prehash: string;
  txid_hex: string;
  raw_tx_hex: string;
}

// Scheme constants
export const SCHEME_DILITHIUM3 = 1;
export const SCHEME_SPHINCS_SHAKE_128S = 2;
export const SCHEME_ML_DSA_65 = 11; // chain v2 default (node scheme_id=11)

// Algorithm ID constants (used in some contexts)
export const ALG_ID_DILITHIUM3 = 0x1001; // 4097 (deprecated stub)
export const ALG_ID_SPHINCS = 0x1002;     // 4098 (deprecated stub)
export const ALG_ID_ML_DSA_65 = 0x1003;   // 4099 — real FIPS 204 ML-DSA-65

// Prehash constants
export const PREHASH_NONE = 0;
export const PREHASH_SHA3_256 = 1;
export const PREHASH_SHA3_512 = 2;

// Domain constant
export const DOMAIN_TX_SIGN = "animica.tx.v1";

// Key and signature lengths
export const KEY_LENGTHS = {
  [SCHEME_DILITHIUM3]: {
    pubkey: 1952,
    signature: 3293,
    name: "dilithium3",
  },
  [SCHEME_SPHINCS_SHAKE_128S]: {
    // Animica's pure-Python SPHINCS-SHAKE-128s variant: pk is derived as
    // _h("pk", sk, out_len=64) → 64 bytes (not the NIST-spec 32 bytes).
    // Matches python/_build_vendor/pq/py/algs/pure_python_fallbacks.py
    // and the on-disk wallets.json format.
    pubkey: 64,
    signature: 7856,
    name: "sphincs_shake_128s",
  },
  [SCHEME_ML_DSA_65]: {
    // FIPS 204 ML-DSA-65 (alg_id 0x1003) — node default. Sizes per the
    // vendored dilithium_py_v2 / @noble/post-quantum ml_dsa65.
    pubkey: 1952,
    signature: 3309,
    name: "ml_dsa_65",
  },
} as const;

export function getSchemeInfo(scheme_id: number) {
  return KEY_LENGTHS[scheme_id as keyof typeof KEY_LENGTHS] || null;
}

export function algIdToSchemeId(alg_id: number): number | null {
  if (alg_id === ALG_ID_DILITHIUM3) return SCHEME_DILITHIUM3;
  if (alg_id === ALG_ID_SPHINCS) return SCHEME_SPHINCS_SHAKE_128S;
  if (alg_id === ALG_ID_ML_DSA_65) return SCHEME_ML_DSA_65;
  return null;
}

export function schemeIdToAlgId(scheme_id: number): number | null {
  if (scheme_id === SCHEME_DILITHIUM3) return ALG_ID_DILITHIUM3;
  if (scheme_id === SCHEME_SPHINCS_SHAKE_128S) return ALG_ID_SPHINCS;
  if (scheme_id === SCHEME_ML_DSA_65) return ALG_ID_ML_DSA_65;
  return null;
}
