/**
 * Core chain types for @animica/sdk (TypeScript).
 *
 * These interfaces mirror the JSON-RPC views served by the node (rpc/*) and the
 * canonical types used by the SDK. They are intentionally permissive (some
 * fields are optional) to allow forward-compatible decoding across minor
 * releases.
 *
 * Conventions:
 *  - Hex strings are 0x-prefixed, lowercase.
 *  - Addresses are bech32m (e.g., "anim1..."). We keep them as opaque strings.
 *  - Big numerics use bigint in memory; wire encodings may be hex strings.
 *  - Timestamps are UNIX seconds (number).
 */

//// ────────────────────────────────────────────────────────────────────────────
// Hex & branded primitive aliases
////

/** 0x-prefixed lowercase hex string */
export type Hex = `0x${string}`

/** 32-byte (or similar) hex digest (0x…) */
export type Hash = Hex & { readonly __brand: 'Hash' }

/** Bech32m address (e.g., anim1…) */
export type Address = string & { readonly __brand: 'Address' }

//// ────────────────────────────────────────────────────────────────────────────
// Access lists & logs
////

export interface AccessListEntry {
  address: Address
  storageKeys: Hash[]
}

/** Canonical access-list (order is significant/canonicalized at encode time) */
export type AccessList = ReadonlyArray<AccessListEntry>

/** Event/log emitted by execution */
export interface LogEvent {
  address: Address
  /** Keccak topic hashes, 0 or more */
  topics: ReadonlyArray<Hash>
  /** ABI-opaque data payload */
  data: Hex
}

//// ────────────────────────────────────────────────────────────────────────────
// Transaction types
////

export type TxKind = 'transfer' | 'deploy' | 'call' | 'blob'

export interface TxCommon {
  kind: TxKind
  chainId: number
  from: Address
  /** Sender nonce (account sequence) */
  nonce: bigint
  /** Total gas limit for this tx */
  gasLimit: bigint
  /** Fee model: base/tip split; both optional for networks with fixed prices */
  maxFeePerGas?: bigint
  maxPriorityFeePerGas?: bigint
  /** Optional access list (hints for scheduling/conflict detection) */
  accessList?: AccessList
}

/** Value transfer between accounts */
export interface TransferTx extends TxCommon {
  kind: 'transfer'
  to: Address
  value: bigint
}

/** Contract deployment (Python-VM). Code is a canonical artifact bytes payload. */
export interface DeployTx extends TxCommon {
  kind: 'deploy'
  /** Contract code bytes (chain-validated format, e.g., VM IR/package) */
  code: Hex
  /** Optional manifest/ABI metadata (client-side convenience; not always in raw tx) */
  manifest?: unknown
  /** Optional constructor call payload */
  init?: Hex
  /** Optional value sent to treasury or contract at deploy */
  value?: bigint
}

/** Contract call to an existing address */
export interface CallTx extends TxCommon {
  kind: 'call'
  to: Address
  /** ABI-encoded calldata (SDK will help build it) */
  data: Hex
  /** Optional value transfer alongside the call */
  value?: bigint
}

/** Blob-carrying transaction (ties into DA module); commonly references a commitment */
export interface BlobTx extends TxCommon {
  kind: 'blob'
  /** Commitment to the posted blob (DA root or NMT root) */
  commitment: Hash
  /** Optional per-blob namespace or hints */
  namespace?: number
}

export type UnsignedTx = TransferTx | DeployTx | CallTx | BlobTx

/** Signature envelope for PQ (e.g., Dilithium3 / SPHINCS+). */
export interface TxSignature {
  /** Canonical algorithm id (per pq/alg_ids.yaml) */
  algId: number
  /** Public key bytes (algorithm-dependent) */
  publicKey: Hex
  /** Signature bytes (domain-separated over SignBytes) */
  signature: Hex
}

/** Signed transaction as accepted by the node */
export interface SignedTx extends UnsignedTx {
  /** CBOR-encoded SignBytes digest is implied by raw encode; hash is view-only */
  hash?: Hash
  /** Signature envelope */
  sig: TxSignature
}

/** Minimal view returned by tx.getTransactionByHash (fields may be subset) */
export interface TxView extends SignedTx {
  /** Inclusion context if mined */
  blockHash?: Hash
  blockNumber?: number
  /** Position within block if mined */
  index?: number
  /** Size (bytes) of the encoded tx (diagnostic) */
  sizeBytes?: number
}

//// ────────────────────────────────────────────────────────────────────────────
// Receipts
////

export type TxStatus = 'SUCCESS' | 'REVERT' | 'OOG'

export interface Receipt {
  txHash: Hash
  /** Present if mined */
  blockHash?: Hash
  blockNumber?: number
  index?: number
  status: TxStatus
  /** Total gas used by this transaction */
  gasUsed: bigint
  /** Optional cumulative gas used up to this tx index (for compatibility) */
  cumulativeGasUsed?: bigint
  logs: ReadonlyArray<LogEvent>
  /** Optional bloom/merkle data; networks may omit in light views */
  logsRoot?: Hash
}

//// ────────────────────────────────────────────────────────────────────────────
// Headers, blocks, and head
////

/** Block header (subset; node may add more roots as features evolve) */
export interface Header {
  chainId: number
  height: number
  hash: Hash
  parentHash: Hash

  /** Execution/state roots */
  stateRoot: Hash
  txRoot: Hash
  receiptsRoot: Hash

  /** Proofs root (PoIES receipts), optional until proofs are finalized in view */
  proofsRoot?: Hash
  /** Data availability root (NMT root) */
  daRoot?: Hash

  /** Θ micro-threshold (fixed-point), used by PoIES */
  thetaMicro: number

  /** Entropy for mining u-draws / mix domain (byte string) */
  mixSeed: Hex

  /** Header timestamp (seconds since epoch) */
  time: number

  /** Optional fields maintained by some networks */
  nonce?: Hex
}

/** Block view returned by RPC. Transactions may be full or hashes depending on flags. */
export interface BlockView {
  header: Header
  /** When `includeTxs=true` in RPC, these are SignedTx; otherwise Hash[] */
  txs: ReadonlyArray<SignedTx | Hash>
  /** Optional receipts when requested */
  receipts?: ReadonlyArray<Receipt>
  /** Optional embedded proofs set */
  proofs?: unknown
  /** Size of encoded block in bytes (diagnostic) */
  sizeBytes?: number
}

/** Light head view for the latest canonical head */
export interface Head {
  chainId: number
  height: number
  hash: Hash
  time: number
  thetaMicro: number
}

//// ────────────────────────────────────────────────────────────────────────────
// Type guards (best-effort; not exhaustive)
////

export function isHex(x: unknown): x is Hex {
  return typeof x === 'string' && /^0x[0-9a-f]*$/.test(x)
}

export function isHash(x: unknown): x is Hash {
  return isHex(x) && (x.length === 66 || x.length === 130 || x.length >= 10) // allow future widths
}

export function isAddress(x: unknown): x is Address {
  return typeof x === 'string' && /^[a-z0-9]{1,83}1[02-9ac-hj-np-z]{6,}$/i.test(x) // bech32 pattern-ish
}

export function isSignedTx(t: UnsignedTx | SignedTx): t is SignedTx {
  return !!(t as any)?.sig
}

export function isReceipt(r: unknown): r is Receipt {
  const v = r as Receipt
  return !!v && isHash(v.txHash) && typeof v.status === 'string' && Array.isArray(v.logs)
}

export function isHeader(h: unknown): h is Header {
  const v = h as Header
  return !!v && typeof v.height === 'number' && isHash(v.hash) && isHash(v.parentHash)
}

export function isBlockView(b: unknown): b is BlockView {
  const v = b as BlockView
  return !!v && isHeader(v.header) && Array.isArray(v.txs)
}

export function isHead(h: unknown): h is Head {
  const v = h as Head
  return !!v && typeof v.height === 'number' && isHash(v.hash)
}

export default {
  // primitives
  Hex: undefined as unknown as Hex,
  Hash: undefined as unknown as Hash,
  Address: undefined as unknown as Address,
  // unions
  TxKind: undefined as unknown as TxKind,
  // interfaces
  AccessListEntry: undefined as unknown as AccessListEntry,
  LogEvent: undefined as unknown as LogEvent,
  TxCommon: undefined as unknown as TxCommon,
  TransferTx: undefined as unknown as TransferTx,
  DeployTx: undefined as unknown as DeployTx,
  CallTx: undefined as unknown as CallTx,
  BlobTx: undefined as unknown as BlobTx,
  SignedTx: undefined as unknown as SignedTx,
  TxView: undefined as unknown as TxView,
  Receipt: undefined as unknown as Receipt,
  Header: undefined as unknown as Header,
  BlockView: undefined as unknown as BlockView,
  Head: undefined as unknown as Head
}
