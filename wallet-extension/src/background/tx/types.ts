/**
 * Transaction & Receipt types for the wallet extension.
 * These align with the canonical shapes defined in core/types/tx.py, adapted to TS.
 *
 * Conventions:
 *  - Amount-like values are decimal strings to avoid JS number precision loss.
 *  - Binary blobs are Uint8Array (hex handled at the edges by utils/bytes.ts).
 *  - Addresses are bech32m strings ("anim1...").
 *  - Hashes are 0x-prefixed hex strings.
 */

export type Address = string; // bech32m (anim1...)
export type Hash = `0x${string}`;
export type Bytes = Uint8Array;

/* --------------------------------- enums ---------------------------------- */

export type TxKind = "transfer" | "call" | "deploy";

export type SigScheme = "dilithium3" | "sphincs_shake_128s";

/** Execution outcome classification (subset shown to UIs). */
export type ReceiptStatus =
  | "success"
  | "revert"            // explicit vm.revert()
  | "out_of_gas"        // gas meter exhausted
  | "invalid"           // failed validation, bad nonce, etc.
  | "failed";           // catch-all for other runtime errors

/* ------------------------------ tx structures ----------------------------- */

/** Fields common to all tx kinds. Mirrors core/types/tx.py:TxCommon. */
export interface TxCommon {
  chainId: number;          // small uint (fits JS number)
  from: Address;            // signer address
  nonce: number;            // per-account
  gasLimit: number;         // upper bound on execution steps (soft cap)
  maxFee: string;           // decimal string amount (fee budget)
  /** Optional memo/opaque data for user wallets (not executed). */
  memo?: string;
}

/** Transfer of native units to an address. Mirrors TxTransfer. */
export interface TxTransfer extends TxCommon {
  kind: "transfer";
  to: Address;
  value: string;            // decimal string amount
}

/** Contract call with ABI-encoded calldata. Mirrors TxCall. */
export interface TxCall extends TxCommon {
  kind: "call";
  to: Address;              // target contract
  data: Bytes;              // ABI-encoded args (method selector inside)
  value?: string;           // optional native value sent along (decimal string)
}

/** Contract deploy with code blob (+ optional init calldata). Mirrors TxDeploy. */
export interface TxDeploy extends TxCommon {
  kind: "deploy";
  code: Bytes;              // compiled code/IR bundle (consensus form)
  init?: Bytes;             // optional constructor/init call data
}

/** Union of all transaction bodies (pre-signature). */
export type TxBody = TxTransfer | TxCall | TxDeploy;

/** Signature & public key container. Mirrors core/types/tx.py:Signature. */
export interface TxSignature {
  scheme: SigScheme;
  pubkey: Bytes;            // raw public key bytes (scheme-dependent)
  sig: Bytes;               // signature bytes
}

/** Signed transaction envelope. Mirrors core/types/tx.py:SignedTx. */
export interface SignedTx {
  body: TxBody;             // canonical tx body
  signature: TxSignature;   // PQ signature over SignBytes(domain|body)
  /** Deterministic tx id (hash of sign bytes), filled after encode. */
  txHash?: Hash;
}

/* -------------------------------- receipts -------------------------------- */

/** Canonical event log structure emitted by contracts. */
export interface EventLog {
  /** Address emitting the event. */
  address: Address;
  /** Event name (utf-8) as emitted by stdlib.events.emit(name, args). */
  name: string;
  /** CBOR-encoded args (raw) for lossless round-trips. */
  data: Bytes;
  /** Best-effort decoded args (JSON-friendly), if available. */
  args?: Record<string, unknown>;
  /** Log index within the tx receipt. */
  index: number;
}

/** Receipt returned once tx is included in a block. Mirrors Receipt in core. */
export interface Receipt {
  txHash: Hash;
  blockHash: Hash;
  blockHeight: number;
  index: number;            // transaction index within the block
  status: ReceiptStatus;
  gasUsed: number;
  /** Return data from a successful call/deploy (ABI-encoded). */
  returnData?: Bytes;
  /** Newly created contract address (for deploy). */
  contractAddress?: Address;
  /** Event logs emitted during execution. */
  logs: EventLog[];
  /** Optional human-readable error for UI (non-consensus). */
  error?: string;
}

/* ----------------------------- rpc view helpers --------------------------- */

/** Lightweight receipt summary used by lists/feeds. */
export interface ReceiptSummary {
  txHash: Hash;
  blockHeight: number;
  status: ReceiptStatus;
  gasUsed: number;
  to?: Address;
  from: Address;
  contractAddress?: Address;
}

/** Pending tx state tracked by the background. */
export interface PendingTx {
  tx: SignedTx;
  submittedAt: number;  // ms since epoch
  /** Local optimistic status until a receipt lands. */
  state: "queued" | "submitted" | "mined" | "dropped" | "replaced" | "error";
  error?: string;
}

/* ----------------------------- simulation types --------------------------- */

/** Result of a preflight/dry-run (no state writes). */
export interface SimulateResult {
  ok: boolean;
  gasUsed: number;
  returnData?: Bytes;
  logs: EventLog[];
  error?: string; // revert reason or validation error (non-consensus)
}

/* --------------------------------- guards --------------------------------- */

export function isTxTransfer(tx: TxBody): tx is TxTransfer {
  return tx.kind === "transfer";
}
export function isTxCall(tx: TxBody): tx is TxCall {
  return tx.kind === "call";
}
export function isTxDeploy(tx: TxBody): tx is TxDeploy {
  return tx.kind === "deploy";
}

/* --------------------------------- helpers -------------------------------- */

/** Narrower address format check (bech32m anim1...), UI-only (non-consensus). */
export function looksLikeAddress(a: string): boolean {
  return typeof a === "string" && /^anim1[0-9a-z]{10,}$/i.test(a);
}

/** Narrower hex-hash format check. */
export function looksLikeHash(h: string): h is Hash {
  return /^0x[0-9a-fA-F]{32,}$/.test(h);
}
