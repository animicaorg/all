/* -----------------------------------------------------------------------------
 * Explorer Core Types
 *
 * Lightweight summary shapes for heads/blocks/txs/receipts/proofs used by the
 * Explorer UI. These are intentionally permissive (lots of optional fields) so
 * they can adapt to different node RPCs while remaining strongly typed.
 * -------------------------------------------------------------------------- */

export type Hex = `0x${string}`;

/**
 * Address string. Typically bech32 (e.g. "omni1...") or hex ("0x...").
 * Keep it as a string to remain display- and transport-friendly.
 */
export type Address = string;

/* -----------------------------------------------------------------------------
 * Head / Block
 * -------------------------------------------------------------------------- */

export interface HeadSummary {
  /** Canonical height (a.k.a. number). */
  number: number;
  /** Block hash at this height. */
  hash: Hex;
  /** Parent block hash. */
  parentHash: Hex;
  /** Block timestamp (seconds since epoch; may be a float). */
  timestamp: number;
  /** Total txs in the block. */
  txCount: number;

  /** Optional execution/resource stats. */
  gasUsed?: number;
  gasLimit?: number;
  sizeBytes?: number;

  /** Optional roots/commitments. */
  stateRoot?: Hex;
  receiptsRoot?: Hex;
  daRoot?: Hex;

  /** Optional proposer/miner address. */
  proposer?: Address;

  /**
   * Optional PoIES / fairness metrics surfaced by some nodes.
   * Fields are optional to avoid tight coupling to a single implementation.
   */
  poies?: {
    /** Γ (Gamma) aggregate. */
    gamma?: number;
    /** ψ (psi) breakdown per bucket/participant. */
    psi?: Record<string, number>;
    /** Fairness indexes. */
    fairness?: {
      /** Gini index (0..1). */
      gini?: number;
      /** Herfindahl–Hirschman index (0..1). */
      hhi?: number;
    };
  };
}

export interface BlockSummary {
  number: number;
  hash: Hex;
  parentHash: Hex;
  timestamp: number;

  /** Number of txs in the block; `txs` may be omitted for summary calls. */
  txCount: number;
  /** Optional full tx list (populated when expanded/fetched). */
  txs?: TxSummary[];

  /** Execution/resource stats. */
  gasUsed?: number;
  gasLimit?: number;
  sizeBytes?: number;

  /** Optional roots/commitments. */
  stateRoot?: Hex;
  receiptsRoot?: Hex;
  daRoot?: Hex;

  proposer?: Address;
}

/* -----------------------------------------------------------------------------
 * Transactions / Receipts / Logs
 * -------------------------------------------------------------------------- */

export type TxKind = 'transfer' | 'call' | 'deploy' | 'unknown';

export interface FeeSummary {
  /** Max gas the tx is allowed to consume. */
  gasLimit: number;
  /**
   * Gas price fields: some networks expose legacy gasPrice, others EIP-1559-like
   * maxFeePerGas / priorityFeePerGas. Use decimal strings for large values.
   */
  gasPrice?: string;
  maxFeePerGas?: string;
  priorityFeePerGas?: string;
  /** Optional fee currency/denom if multi-asset gas is supported. */
  denom?: string;
}

export interface TxSummary {
  /** Transaction hash. */
  hash: Hex;

  /** Sender and (optional) recipient. */
  from: Address;
  to?: Address;

  /** Nonce / sequence number. */
  nonce: number;

  /** High-level classification for UI. */
  kind: TxKind;

  /** Optional value (amount transferred) as a decimal string. */
  value?: string;

  /** Fee / gas parameters. */
  fee?: FeeSummary;

  /** Encoded input payload (hex). */
  data?: Hex;

  /** Optional CBOR-encoded sign bytes (hex) – for debugging/inspection. */
  cbor?: Hex;

  /** Optional human memo/tag. */
  memo?: string;
}

export interface EventLog {
  /** Index of the log within the tx receipt. */
  index: number;

  /** Emitting contract/account. */
  address: Address;

  /** Topic list (indexed fields), hex-encoded. */
  topics: Hex[];

  /** Opaque data payload, hex-encoded. */
  data: Hex;

  /** Linking info for navigation. */
  txHash: Hex;
  blockHash: Hex;
  blockNumber: number;

  /** Optional decoded event name. */
  event?: string;

  /** Optional decoded key/value args (ABI-derived). */
  decoded?: Record<string, unknown>;
}

export interface ReceiptSummary {
  /** Linked tx and block info. */
  txHash: Hex;
  blockHash: Hex;
  blockNumber: number;
  /** Index of tx within the block. */
  index: number;

  /** Success flag; if false, `error` may contain a message/code. */
  success: boolean;
  error?: string;

  /** Gas consumed by this transaction. */
  gasUsed?: number;

  /** Present when the tx deployed a contract. */
  contractAddress?: Address;

  /** Emitted logs. */
  logs?: EventLog[];
}

/* -----------------------------------------------------------------------------
 * Proofs (light-client / DA)
 * -------------------------------------------------------------------------- */

export interface HeaderProof {
  kind: 'header';
  /** Verified header hash. */
  hash: Hex;
  /** Parent linkage (if provided by RPC). */
  parentHash?: Hex;
  /** Optional merkle path or succinct proof blob. */
  proof?: Hex | string;
}

export interface DAProof {
  kind: 'da';
  /** Namespaced Merkle Tree root (or equivalent commitment). */
  root: Hex;
  /** Total size in bytes of the original blob. */
  totalSize?: number;
  /** Number of shares/rows/cols included (implementation-specific). */
  shares?: number;
  rows?: number;
  cols?: number;
  /** Optional compact proof encoding. */
  proof?: Hex | string;
  /** Optional namespace(s) used. */
  namespaces?: Hex[];
}

export interface LightClientProof {
  kind: 'light';
  /** Header hash being proven. */
  hash: Hex;
  /** Number of signatures included. */
  signatures?: number;
  /** Committee or validator set identifier. */
  committeeId?: string;
  /** Optional compact proof encoding. */
  proof?: Hex | string;
}

export type ProofSummary = HeaderProof | DAProof | LightClientProof;

/* -----------------------------------------------------------------------------
 * Generic envelope for API responses that attach server-side timing.
 * -------------------------------------------------------------------------- */
export interface WithMeta<T> {
  data: T;
  /** Server-produced timestamp (seconds). */
  t: number;
}

/* -----------------------------------------------------------------------------
 * Type guards (useful in UI components)
 * -------------------------------------------------------------------------- */
export function isHeaderProof(p: ProofSummary): p is HeaderProof {
  return p.kind === 'header';
}
export function isDAProof(p: ProofSummary): p is DAProof {
  return p.kind === 'da';
}
export function isLightClientProof(p: ProofSummary): p is LightClientProof {
  return p.kind === 'light';
}
