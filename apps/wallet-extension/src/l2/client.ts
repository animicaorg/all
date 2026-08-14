// ANM Instant (L2) client
//
// Wraps the extension's existing JSON-RPC transport (core/rpc/client.ts's
// RpcClient) with typed L2 methods. The node exposes L2 on the SAME endpoint
// as L1 with `l2_`-prefixed methods, so this client takes any object that can
// `call(method, params)` — an RpcClient satisfies that structurally.
//
// The high-level sendInstant()/withdrawToL1() run the canonical wallet signing
// recipe:
//   1. l2_prepareTransfer -> { signingHash, bodyHex, fee, nonce }
//   2. sign the 64-byte signingHash DIRECTLY with the account's existing
//      ML-DSA-65 signer (alg 0x1003) — NO re-hashing. The L2 address is
//      identical to the L1 address (sha3_256(u16be(algId)||pubkey)), so the
//      user's existing key works on L2 unchanged.
//   3. l2_submitSigned({ body, pubkey, signature }) -> txid
//   4. poll l2_getTransaction until PROVEN — SOFT_CONFIRMED (sequencer
//      acceptance) is NEVER presented as L1 settlement.

import { bytesToHex, hexToBytes } from '../core/crypto/convert';

/** Lifecycle statuses reported by l2_getTransaction. */
export type L2TxStatus =
  | 'RECEIVED'
  | 'VALIDATED'
  | 'SOFT_CONFIRMED'
  | 'BATCHED'
  | 'PROVEN'
  | 'L1_SUBMITTED'
  | 'L1_FINALIZED'
  | 'FAILED'
  | 'REVERTED';

/** Statuses at or beyond L1-provable finality. */
const PROVEN_OR_BEYOND = new Set<string>(['PROVEN', 'L1_SUBMITTED', 'L1_FINALIZED']);
/** Terminal failure statuses. */
const TERMINAL_FAILURE = new Set<string>(['FAILED', 'REVERTED']);

/**
 * Minimal transport contract. `RpcClient` from core/rpc/client.ts satisfies
 * this — its `call(method, params, schema?, options?)` is call-compatible with
 * `call(method, params?)`.
 */
export interface L2Transport {
  call(method: string, params?: Record<string, unknown> | unknown[]): Promise<any>;
}

/**
 * Signing material for the active account. `signHash` MUST sign the raw
 * message bytes directly (the caller passes the 64-byte L2 signingHash) with
 * the account's ML-DSA-65 key — the same primitive used for L1.
 */
export interface L2Signer {
  address: string;
  publicKey: Uint8Array;
  algId: number;
  signHash(hash: Uint8Array): Promise<Uint8Array>;
}

export interface L2BridgeStatus {
  depositAddress?: string;
  [key: string]: unknown;
}

export interface L2Status {
  enabled: boolean;
  mode?: string;
  l2ChainId?: number;
  settlementMode?: string;
  headBatch?: number;
  stateRoot?: string;
  pending?: number;
  sigBackend?: string;
  bridge?: L2BridgeStatus;
  [key: string]: unknown;
}

export interface L2Balance {
  address: string;
  /** Balance in nanos (1 ANM = 1e9). */
  balance: string;
  nonce: number;
  pendingNonce: number;
  unit: string;
}

export type L2TransferKind = 'transfer' | 'pay' | 'withdraw';

export interface L2PrepareParams {
  kind: L2TransferKind;
  sender: string;
  recipient: string;
  /** Integer nanos. */
  amount: string;
  memo?: string;
  nonce?: number;
  fee?: string;
  expiry?: number;
}

export interface L2PreparedTransfer {
  kind: L2TransferKind;
  sender: string;
  recipient: string;
  amount: string;
  nonce: number;
  fee: string;
  requiredFee: string;
  l2ChainId: number;
  /** 0x-hex canonical body — submit this verbatim. */
  bodyHex: string;
  /** 0x-hex 64-byte sha3-512 digest to sign directly. */
  signingHash: string;
  sigScheme: string;
}

export interface L2SubmitParams {
  /** bodyHex from l2_prepareTransfer. */
  body: string;
  /** 0x-hex ML-DSA-65 public key (1952 bytes). */
  pubkey: string;
  /** 0x-hex ML-DSA-65 signature (3309 bytes). */
  signature: string;
}

export interface L2TransactionInfo {
  txid: string;
  status: L2TxStatus;
  batch?: number | null;
  receipt?: unknown;
  reason?: string | null;
  receivedMs?: number;
  [key: string]: unknown;
}

export interface L2Tps {
  [key: string]: unknown;
}

export interface L2WithdrawalProof {
  nullifier: string;
  [key: string]: unknown;
}

/** Accepts integer nanos as bigint / number / decimal-free string. */
export type NanoAmount = bigint | number | string;

export interface PollOptions {
  timeoutMs?: number;
  intervalMs?: number;
  /** Optional injectable sleep — defaults to setTimeout. Handy for tests. */
  sleep?: (ms: number) => Promise<void>;
}

export interface InstantSendResult {
  txid: string;
  status: L2TxStatus;
  /** True only once the tx reaches PROVEN or later — safe to call "settled". */
  proven: boolean;
  prepared: L2PreparedTransfer;
  receipt: L2TransactionInfo | null;
}

export interface WithdrawResult extends InstantSendResult {
  /** Present once a withdrawal nullifier + L1 claim data is available. */
  withdrawalProof: L2WithdrawalProof | null;
}

const DEFAULT_POLL_TIMEOUT_MS = 60_000;
const DEFAULT_POLL_INTERVAL_MS = 1_500;

function defaultSleep(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

function toNanosString(amount: NanoAmount, field = 'amount'): string {
  if (typeof amount === 'bigint') {
    if (amount < 0n) throw new Error(`${field} must be a non-negative integer (nanos)`);
    return amount.toString();
  }
  if (typeof amount === 'number') {
    if (!Number.isInteger(amount) || amount < 0) {
      throw new Error(`${field} must be a non-negative integer (nanos)`);
    }
    return amount.toString();
  }
  const trimmed = String(amount).trim();
  if (!/^\d+$/.test(trimmed)) {
    throw new Error(`${field} must be a non-negative integer string (nanos)`);
  }
  return trimmed;
}

export class L2Client {
  private readonly transport: L2Transport;

  constructor(transport: L2Transport) {
    this.transport = transport;
  }

  // --- low-level typed methods ------------------------------------------

  async l2ChainId(): Promise<number> {
    return Number(await this.transport.call('l2_chainId', []));
  }

  async l2Status(): Promise<L2Status> {
    return this.transport.call('l2_status', []);
  }

  async l2GetBalance(address: string): Promise<L2Balance> {
    return this.transport.call('l2_getBalance', { address });
  }

  async l2PrepareTransfer(params: L2PrepareParams): Promise<L2PreparedTransfer> {
    return this.transport.call('l2_prepareTransfer', { ...params });
  }

  async l2SubmitSigned(params: L2SubmitParams): Promise<string> {
    return this.transport.call('l2_submitSigned', { ...params });
  }

  async l2GetTransaction(txid: string): Promise<L2TransactionInfo> {
    return this.transport.call('l2_getTransaction', { txid });
  }

  async l2GetTPS(): Promise<L2Tps> {
    return this.transport.call('l2_getTPS', []);
  }

  async l2EstimateFee(rawBodyHex: string): Promise<any> {
    return this.transport.call('l2_estimateFee', { raw: rawBodyHex });
  }

  async l2GetStateRoot(): Promise<any> {
    return this.transport.call('l2_getStateRoot', []);
  }

  async l2GetWithdrawalProof(nullifier: string): Promise<L2WithdrawalProof> {
    return this.transport.call('l2_getWithdrawalProof', { nullifier });
  }

  // --- signing recipe ---------------------------------------------------

  /**
   * Sign a prepared transfer and submit it. Signs the returned 64-byte
   * signingHash DIRECTLY (no re-hash), then calls l2_submitSigned with the
   * account pubkey + signature as 0x-hex. Returns the txid.
   */
  async signAndSubmit(prepared: L2PreparedTransfer, signer: L2Signer): Promise<string> {
    const hashBytes = hexToBytes(prepared.signingHash, 'signingHash');
    if (hashBytes.length !== 64) {
      throw new Error(`L2 signingHash must be 64 bytes, got ${hashBytes.length}`);
    }
    const signature = await signer.signHash(hashBytes);
    return this.l2SubmitSigned({
      body: prepared.bodyHex,
      pubkey: bytesToHex(signer.publicKey, 'pubkey'),
      signature: bytesToHex(signature, 'signature'),
    });
  }

  /**
   * Poll l2_getTransaction until the tx reaches PROVEN (or later). Throws on
   * FAILED/REVERTED. On timeout returns the last-seen state WITHOUT throwing —
   * callers must check `.status`/proven and must NOT treat SOFT_CONFIRMED as
   * L1 settlement.
   */
  async waitForProven(txid: string, opts: PollOptions = {}): Promise<L2TransactionInfo> {
    const timeoutMs = opts.timeoutMs ?? DEFAULT_POLL_TIMEOUT_MS;
    const intervalMs = opts.intervalMs ?? DEFAULT_POLL_INTERVAL_MS;
    const sleep = opts.sleep ?? defaultSleep;
    const deadline = Date.now() + timeoutMs;

    let last: L2TransactionInfo = { txid, status: 'RECEIVED' };
    // Always poll at least once so a fast sequencer path is picked up even
    // when the timeout is tiny.
    for (;;) {
      const tx = await this.l2GetTransaction(txid);
      if (tx) last = tx;
      const status = String(tx?.status ?? '');
      if (PROVEN_OR_BEYOND.has(status)) return tx;
      if (TERMINAL_FAILURE.has(status)) {
        throw new Error(`L2 transaction ${status}: ${tx?.reason ?? 'no reason given'}`);
      }
      if (Date.now() + intervalMs >= deadline) break;
      await sleep(intervalMs);
    }
    return last;
  }

  // --- high-level flows -------------------------------------------------

  /**
   * Prepare -> sign -> submit -> poll an ANM Instant (L2) transfer.
   * `amount` is integer nanos.
   */
  async sendInstant(
    intent: { to: string; amount: NanoAmount; memo?: string; nonce?: number; fee?: NanoAmount },
    signer: L2Signer,
    poll: PollOptions = {},
  ): Promise<InstantSendResult> {
    const prepared = await this.l2PrepareTransfer({
      kind: 'transfer',
      sender: signer.address,
      recipient: intent.to,
      amount: toNanosString(intent.amount),
      memo: intent.memo,
      nonce: intent.nonce,
      fee: intent.fee !== undefined ? toNanosString(intent.fee, 'fee') : undefined,
    });
    const txid = await this.signAndSubmit(prepared, signer);
    const receipt = await this.waitForProven(txid, poll);
    const status = (receipt?.status ?? 'RECEIVED') as L2TxStatus;
    return {
      txid,
      status,
      proven: PROVEN_OR_BEYOND.has(status),
      prepared,
      receipt: receipt ?? null,
    };
  }

  /**
   * Withdraw L2 -> L1 via the same prepare/sign/submit flow (kind="withdraw").
   * After proving, best-effort fetches the L1 withdrawal claim data when the
   * receipt exposes a nullifier.
   */
  async withdrawToL1(
    intent: { to: string; amount: NanoAmount; memo?: string; nonce?: number; fee?: NanoAmount },
    signer: L2Signer,
    poll: PollOptions = {},
  ): Promise<WithdrawResult> {
    const prepared = await this.l2PrepareTransfer({
      kind: 'withdraw',
      sender: signer.address,
      recipient: intent.to,
      amount: toNanosString(intent.amount),
      memo: intent.memo,
      nonce: intent.nonce,
      fee: intent.fee !== undefined ? toNanosString(intent.fee, 'fee') : undefined,
    });
    const txid = await this.signAndSubmit(prepared, signer);
    const receipt = await this.waitForProven(txid, poll);
    const status = (receipt?.status ?? 'RECEIVED') as L2TxStatus;

    let withdrawalProof: L2WithdrawalProof | null = null;
    const nullifier = extractNullifier(receipt);
    if (nullifier) {
      try {
        withdrawalProof = await this.l2GetWithdrawalProof(nullifier);
      } catch {
        // Non-fatal: the proof may not be claimable until L1 finality. The UI
        // can retry l2_getWithdrawalProof with the nullifier later.
        withdrawalProof = null;
      }
    }

    return {
      txid,
      status,
      proven: PROVEN_OR_BEYOND.has(status),
      prepared,
      receipt: receipt ?? null,
      withdrawalProof,
    };
  }
}

function extractNullifier(receipt: L2TransactionInfo | null | undefined): string | null {
  if (!receipt) return null;
  const direct = (receipt as any).nullifier;
  if (typeof direct === 'string' && direct.length > 0) return direct;
  const r = (receipt as any).receipt;
  if (r && typeof r === 'object' && typeof r.nullifier === 'string' && r.nullifier.length > 0) {
    return r.nullifier;
  }
  return null;
}
