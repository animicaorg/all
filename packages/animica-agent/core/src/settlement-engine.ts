/**
 * Settlement engine for useful-work rewards.
 *
 * Owns the lifecycle of a payout attempt from the moment a receipt is queued
 * for settlement until it reaches a terminal state. Persisted as JSONL so
 * the engine resumes cleanly after restart and never re-pays a confirmed
 * receipt.
 *
 * State machine (intentionally distinct from BillingEngine's coarser
 * receipt status — billing tracks accounting, this tracks chain settlement):
 *
 *   pending_submission ─► submitted ─► confirming ─► confirmed ─► paid
 *           │                 │            │             │
 *           │                 │            │             └──► rejected (on-chain status=0)
 *           ├─► failed_transient (retryable; bumps attempts)
 *           ├─► failed_permanent (chain mismatch, insufficient balance, …)
 *           └─► expired (waited past max attempts / deadline)
 *
 * Failure classes are exposed verbatim so operators can route them:
 *   - rpc-unavailable | signing-failure | nonce-conflict
 *   - insufficient-balance | invalid-recipient | chain-mismatch
 *   - tx-dropped | tx-replaced | confirmation-timeout | unknown
 *
 * The engine never mutates Receipt records — it owns its own attempt
 * journal and keys every attempt by `receiptId`. The latest non-terminal
 * attempt is the active attempt; a confirmed/paid attempt locks the receipt.
 */

import { appendFileSync, existsSync, mkdirSync, readFileSync, renameSync, writeFileSync } from "node:fs";
import { createHash, randomUUID } from "node:crypto";
import { join } from "node:path";

import type { AgentConfig } from "./config.js";
import { AgentError } from "./errors.js";
import { isLikelyAnimicaAddress, probeNode } from "./rpc.js";
import { safeParse, safeStringify } from "./safe-json.js";
import { formatANM, type Signer, type SignRequest } from "./wallet.js";
import { classifyTxSendFailure, parseTxHash } from "./signers.js";
import { waitForConfirmation } from "./settlement.js";

export type SettlementStatus =
  | "pending_submission"
  | "submitted"
  | "confirming"
  | "confirmed"
  | "paid"
  | "rejected"
  | "failed_transient"
  | "failed_permanent"
  | "expired";

export const SETTLEMENT_TERMINAL_STATES: ReadonlySet<SettlementStatus> = new Set([
  "paid",
  "rejected",
  "failed_permanent",
  "expired",
]);

export type SettlementFailureReason =
  | "rpc-unavailable"
  | "signing-failure"
  | "nonce-conflict"
  | "insufficient-balance"
  | "invalid-recipient"
  | "chain-mismatch"
  | "tx-dropped"
  | "tx-replaced"
  | "confirmation-timeout"
  | "policy-rejected"
  | "duplicate-receipt"
  | "expired-deadline"
  | "unknown";

export const ALLOWED_SETTLEMENT_TRANSITIONS: Readonly<
  Record<SettlementStatus, ReadonlySet<SettlementStatus>>
> = Object.freeze({
  pending_submission: new Set<SettlementStatus>([
    "submitted",
    "failed_transient",
    "failed_permanent",
    "expired",
  ]),
  submitted: new Set<SettlementStatus>(["confirming", "failed_transient", "failed_permanent", "rejected"]),
  confirming: new Set<SettlementStatus>(["confirmed", "rejected", "failed_transient", "expired"]),
  confirmed: new Set<SettlementStatus>(["paid"]),
  paid: new Set<SettlementStatus>([]),
  rejected: new Set<SettlementStatus>([]),
  failed_transient: new Set<SettlementStatus>([
    "pending_submission",
    "failed_permanent",
    "expired",
  ]),
  failed_permanent: new Set<SettlementStatus>([]),
  expired: new Set<SettlementStatus>([]),
});

export interface SettlementAttempt {
  /** Stable id for this attempt. */
  id: string;
  /** Receipt the attempt settles. */
  receiptId: string;
  /** Idempotency key derived from receiptId + artifact hash. */
  idempotencyKey: string;
  /** Payout target (wallet address). */
  recipient: string;
  /** Amount in smallest unit (BigInt-safe encoded on disk). */
  amountRaw: bigint;
  /** Current state. */
  status: SettlementStatus;
  /** ISO timestamps. */
  createdAt: string;
  updatedAt: string;
  /** Tx hash once we observe it from the signer. */
  txHash?: string;
  /** Confirmation count observed. */
  confirmations?: number;
  /** Block number at which the tx was included. */
  blockNumber?: bigint;
  /** Last failure reason if not terminal-positive. */
  failureReason?: SettlementFailureReason;
  /** Operator-facing message (BigInt-safe). */
  reason?: string;
  /** Number of submit attempts that have produced an on-chain tx. */
  attempts: number;
  /** Per-attempt audit metadata; lets operators trace decisions. */
  decisions: { at: string; from: SettlementStatus; to: SettlementStatus; reason?: string }[];
  /** Settlement-side hash over the attempt body for tamper detection. */
  attemptHash: string;
}

export class InvalidSettlementTransition extends AgentError {
  constructor(from: SettlementStatus, to: SettlementStatus) {
    super("INVALID_SETTLEMENT_TRANSITION", `cannot transition settlement from ${from} to ${to}`);
    this.name = "InvalidSettlementTransition";
  }
}

export function canSettlementTransition(from: SettlementStatus, to: SettlementStatus): boolean {
  if (from === to) return true;
  return ALLOWED_SETTLEMENT_TRANSITIONS[from].has(to);
}

function attemptIdempotencyKey(receiptId: string, artifactHash: string | undefined): string {
  // Stable across retries so duplicate submissions deduplicate at the engine
  // boundary. We don't include attempt id; multiple attempts may share the
  // same idempotency key by design.
  return createHash("sha256")
    .update(`receipt:${receiptId}:${artifactHash ?? "no-artifact"}`)
    .digest("hex");
}

function hashAttempt(a: Omit<SettlementAttempt, "attemptHash">): string {
  return createHash("sha256").update(safeStringify(a)).digest("hex");
}

/**
 * Persistent attempt store. Last record per receiptId is authoritative for
 * idempotency lookup; the JSONL is append-only and compactable offline.
 */
export class SettlementJournal {
  private readonly file: string;
  private latestByReceipt: Map<string, SettlementAttempt> = new Map();
  private allByReceipt: Map<string, SettlementAttempt[]> = new Map();
  private byIdempotencyKey: Map<string, SettlementAttempt> = new Map();
  private loaded = false;

  constructor(stateDir: string) {
    mkdirSync(stateDir, { recursive: true });
    this.file = join(stateDir, "settlement.jsonl");
  }

  path(): string {
    return this.file;
  }

  reload(): void {
    this.latestByReceipt.clear();
    this.allByReceipt.clear();
    this.byIdempotencyKey.clear();
    if (!existsSync(this.file)) {
      this.loaded = true;
      return;
    }
    const text = readFileSync(this.file, "utf8");
    for (const line of text.split(/\r?\n/)) {
      if (!line) continue;
      try {
        const rec = safeParse<SettlementAttempt>(line);
        if (rec && rec.id && rec.receiptId) this.absorb(rec);
      } catch {
        /* tolerate corrupt lines so partial writes don't break recovery */
      }
    }
    this.loaded = true;
  }

  private absorb(rec: SettlementAttempt): void {
    this.latestByReceipt.set(rec.receiptId, rec);
    const arr = this.allByReceipt.get(rec.receiptId) ?? [];
    arr.push(rec);
    this.allByReceipt.set(rec.receiptId, arr);
    // The idempotency key map should always reflect the latest record so
    // that a confirmed-paid record blocks a new pending_submission.
    this.byIdempotencyKey.set(rec.idempotencyKey, rec);
  }

  private ensureLoaded(): void {
    if (!this.loaded) this.reload();
  }

  latest(receiptId: string): SettlementAttempt | null {
    this.ensureLoaded();
    return this.latestByReceipt.get(receiptId) ?? null;
  }

  all(receiptId: string): SettlementAttempt[] {
    this.ensureLoaded();
    return [...(this.allByReceipt.get(receiptId) ?? [])];
  }

  list(): SettlementAttempt[] {
    this.ensureLoaded();
    return [...this.latestByReceipt.values()];
  }

  findByIdempotencyKey(key: string): SettlementAttempt | null {
    this.ensureLoaded();
    return this.byIdempotencyKey.get(key) ?? null;
  }

  /** Append a new attempt. The caller is responsible for transition validity. */
  append(rec: SettlementAttempt): void {
    appendFileSync(this.file, safeStringify(rec) + "\n", "utf8");
    this.absorb(rec);
  }

  /** Compact the journal so only the latest record per receiptId remains. Returns the count dropped. */
  compact(): number {
    this.ensureLoaded();
    if (!existsSync(this.file)) return 0;
    const beforeLines = readFileSync(this.file, "utf8").split(/\r?\n/).filter(Boolean).length;
    const tmp = this.file + ".tmp";
    const body = [...this.latestByReceipt.values()].map((r) => safeStringify(r)).join("\n") + "\n";
    writeFileSync(tmp, body, "utf8");
    renameSync(tmp, this.file);
    return Math.max(0, beforeLines - this.latestByReceipt.size);
  }
}

export interface SettlementEngineOptions {
  signer: Signer;
  journal: SettlementJournal;
  /** Confirmation depth required before a `confirmed` attempt is marked `paid`. */
  confirmationDepth: number;
  /** Max attempts per receipt before forcing `failed_permanent`. */
  maxAttempts: number;
  /** Absolute deadline (ms from createdAt) after which we mark `expired`. */
  attemptDeadlineMs: number;
  /** Between-poll sleep for confirmation watching (ms). */
  confirmIntervalMs: number;
  /** Maximum confirmation polls per call. */
  confirmMaxPolls: number;
  /** Optional onTransition observer (telemetry). */
  onTransition?: (rec: SettlementAttempt) => void;
  /** Sleep shim for tests. */
  sleep?: (ms: number) => Promise<void>;
}

export interface SettlementInput {
  receiptId: string;
  recipient: string;
  amountRaw: bigint;
  artifactHash?: string;
  /** Optional config snapshot used to validate chain id/RPC. */
  config?: AgentConfig;
}

export interface ConfirmationCheck {
  status: "missing" | "pending" | "rejected" | "rpc-error" | "confirmed";
  blockNumber?: bigint;
  confirmations?: number;
  error?: string;
}

export interface ConfirmationPoller {
  /** Returns current chain-side view of a tx. */
  fetchReceipt(txHash: string): Promise<ConfirmationCheck>;
  /** Returns the chain head block number (used for confirmation depth). */
  headBlockNumber(): Promise<bigint | null>;
}

export class RpcConfirmationPoller implements ConfirmationPoller {
  constructor(private readonly rpcUrl: string) {}
  async fetchReceipt(txHash: string): Promise<ConfirmationCheck> {
    const r = await waitForConfirmation(txHash, {
      rpcUrl: this.rpcUrl,
      maxAttempts: 1,
      intervalMs: 0,
      sleep: async () => {},
    });
    if (r.status === "confirmed") {
      return { status: "confirmed", blockNumber: r.receipt?.blockNumber };
    }
    if (r.status === "rejected") return { status: "rejected", blockNumber: r.receipt?.blockNumber };
    if (r.status === "rpc-error") return { status: "rpc-error", error: r.error };
    if (r.status === "missing") return { status: "missing" };
    return { status: "pending" };
  }
  async headBlockNumber(): Promise<bigint | null> {
    const node = await probeNode(this.rpcUrl, 3_000).catch(() => null);
    return node?.blockNumber ?? null;
  }
}

/** Read-only validator the engine consults before each submit attempt. */
export interface PreflightCheck {
  ok: boolean;
  reason?: SettlementFailureReason;
  message?: string;
}

export interface Preflight {
  validate(input: SettlementInput): Promise<PreflightCheck>;
}

/** Default preflight — verifies the recipient is plausibly Animica-shaped. */
export class BasicPreflight implements Preflight {
  async validate(input: SettlementInput): Promise<PreflightCheck> {
    if (!input.recipient || !isLikelyAnimicaAddress(input.recipient)) {
      return {
        ok: false,
        reason: "invalid-recipient",
        message: `recipient is not a likely Animica address: ${input.recipient}`,
      };
    }
    if (input.amountRaw < 0n) {
      return { ok: false, reason: "policy-rejected", message: "amount cannot be negative" };
    }
    return { ok: true };
  }
}

/**
 * The main engine. Stateless aside from the journal — every method derives
 * the latest attempt from the journal so two processes never observe a
 * stale in-memory view.
 */
export class SettlementEngine {
  private readonly opts: SettlementEngineOptions;
  private readonly preflight: Preflight;

  constructor(opts: SettlementEngineOptions, preflight: Preflight = new BasicPreflight()) {
    this.opts = opts;
    this.preflight = preflight;
  }

  /**
   * Queue a settlement attempt for a receipt. Returns the freshly persisted
   * `pending_submission` attempt, or the existing attempt if an idempotent
   * hit was found.
   */
  async queue(input: SettlementInput): Promise<SettlementAttempt> {
    this.opts.journal.reload();
    const key = attemptIdempotencyKey(input.receiptId, input.artifactHash);
    const existing = this.opts.journal.findByIdempotencyKey(key);
    if (existing && (SETTLEMENT_TERMINAL_STATES.has(existing.status) || existing.status === "submitted" || existing.status === "confirming" || existing.status === "confirmed")) {
      // A non-failed prior attempt blocks duplicates.
      return existing;
    }
    const pre = await this.preflight.validate(input);
    if (!pre.ok) {
      return this.record({
        receiptId: input.receiptId,
        recipient: input.recipient,
        amountRaw: input.amountRaw,
        idempotencyKey: key,
        status: "failed_permanent",
        reason: pre.message ?? "preflight rejected",
        failureReason: pre.reason ?? "policy-rejected",
      });
    }
    return this.record({
      receiptId: input.receiptId,
      recipient: input.recipient,
      amountRaw: input.amountRaw,
      idempotencyKey: key,
      status: "pending_submission",
      reason: "queued",
    });
  }

  /** Drive the latest attempt for a receipt forward by one step. */
  async drive(receiptId: string): Promise<SettlementAttempt> {
    this.opts.journal.reload();
    const cur = this.opts.journal.latest(receiptId);
    if (!cur) throw new AgentError("UNKNOWN_SETTLEMENT", `no settlement attempt for receiptId=${receiptId}`);
    if (SETTLEMENT_TERMINAL_STATES.has(cur.status)) return cur;

    // Expiry guard.
    const ageMs = Date.now() - new Date(cur.createdAt).getTime();
    if (ageMs > this.opts.attemptDeadlineMs) {
      return this.transition(cur, "expired", {
        reason: `attempt exceeded deadline (${ageMs}ms > ${this.opts.attemptDeadlineMs}ms)`,
        failureReason: "expired-deadline",
      });
    }
    if (cur.attempts > this.opts.maxAttempts) {
      return this.transition(cur, "failed_permanent", {
        reason: `exceeded maxAttempts=${this.opts.maxAttempts}`,
        failureReason: "expired-deadline",
      });
    }

    switch (cur.status) {
      case "pending_submission":
        return this.submit(cur);
      case "submitted":
      case "confirming":
        return this.poll(cur);
      case "confirmed":
        return this.markPaid(cur);
      case "failed_transient":
        return this.transition(cur, "pending_submission", { reason: "retry after transient failure" });
      default:
        return cur;
    }
  }

  /** One-shot settlement: queue + drive to terminal or non-terminal stall. */
  async settleOnce(input: SettlementInput): Promise<SettlementAttempt> {
    let cur = await this.queue(input);
    let safety = 32; // hard cap to avoid infinite drive loops on programmer error
    while (!SETTLEMENT_TERMINAL_STATES.has(cur.status) && safety-- > 0) {
      const next = await this.drive(cur.receiptId);
      if (next.status === cur.status && next.id === cur.id) break;
      cur = next;
    }
    return cur;
  }

  private async submit(cur: SettlementAttempt): Promise<SettlementAttempt> {
    let signed: Awaited<ReturnType<Signer["sign"]>>;
    try {
      const req: SignRequest = {
        payload: {
          kind: "anm-transfer",
          data: { from: cur.recipient, to: cur.recipient, valueRaw: cur.amountRaw },
        },
        reason: `settle agent receipt ${cur.receiptId} for ${formatANM(cur.amountRaw)} ANM`,
        estimatedCostRaw: cur.amountRaw,
      };
      signed = await this.opts.signer.sign(req);
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      const reason = mapSignerError(msg);
      const isPermanent = ["invalid-recipient", "insufficient-balance", "chain-mismatch", "signing-failure"].includes(
        reason,
      );
      return this.transition(cur, isPermanent ? "failed_permanent" : "failed_transient", {
        reason: `submit error: ${msg.slice(0, 200)}`,
        failureReason: reason,
      });
    }
    const txHash = signed.txHash ?? (signed.signature ? parseTxHash(signed.signature) : undefined);
    if (!txHash) {
      return this.transition(cur, "failed_transient", {
        reason: "signer returned no tx hash",
        failureReason: "signing-failure",
      });
    }
    const submitted = this.transition(cur, "submitted", {
      reason: "tx submitted",
      txHash,
    });
    // Move directly to confirming so the next drive() polls.
    return this.transition(submitted, "confirming", { reason: "polling for confirmation" });
  }

  private async poll(cur: SettlementAttempt): Promise<SettlementAttempt> {
    if (!cur.txHash) {
      return this.transition(cur, "failed_transient", {
        reason: "no txHash recorded on attempt",
        failureReason: "unknown",
      });
    }
    const poller = (this as unknown as { _poller?: ConfirmationPoller })._poller;
    if (!poller) {
      // Engine was constructed without a poller. Fail closed.
      return this.transition(cur, "failed_permanent", {
        reason: "no confirmation poller configured",
        failureReason: "unknown",
      });
    }
    let r: ConfirmationCheck;
    try {
      r = await poller.fetchReceipt(cur.txHash);
    } catch (err) {
      return this.transition(cur, "failed_transient", {
        reason: `confirmation poll error: ${(err as Error).message?.slice(0, 200)}`,
        failureReason: "rpc-unavailable",
      });
    }
    if (r.status === "rejected") {
      return this.transition(cur, "rejected", {
        reason: "tx receipt status=0",
        failureReason: "tx-dropped",
      });
    }
    if (r.status === "missing") {
      // tx not found yet; could be dropped or just slow.
      if ((cur.confirmations ?? 0) === 0) {
        // Bump pending count via decisions; keep state.
        return this.transition(cur, "confirming", {
          reason: "tx not yet seen by node",
        });
      }
      return this.transition(cur, "failed_transient", {
        reason: "tx vanished after previous sighting",
        failureReason: "tx-replaced",
      });
    }
    if (r.status === "rpc-error") {
      return this.transition(cur, "failed_transient", {
        reason: `RPC error: ${r.error ?? "unknown"}`,
        failureReason: "rpc-unavailable",
      });
    }
    if (r.status === "pending") {
      return this.transition(cur, "confirming", { reason: "still pending" });
    }
    // confirmed at chain — check depth.
    const head = await poller.headBlockNumber().catch(() => null);
    const block = r.blockNumber ?? 0n;
    const confirmations = head !== null && head >= block ? Number(head - block) + 1 : 1;
    if (confirmations < this.opts.confirmationDepth) {
      return this.transition(cur, "confirming", {
        reason: `${confirmations}/${this.opts.confirmationDepth} confirmations`,
        blockNumber: block,
        confirmations,
      });
    }
    return this.transition(cur, "confirmed", {
      reason: `${confirmations} confirmations ≥ ${this.opts.confirmationDepth}`,
      blockNumber: block,
      confirmations,
    });
  }

  private markPaid(cur: SettlementAttempt): SettlementAttempt {
    return this.transition(cur, "paid", { reason: "settlement complete" });
  }

  /** Attach a confirmation poller (kept off the constructor signature for late binding). */
  attachPoller(p: ConfirmationPoller): void {
    (this as unknown as { _poller: ConfirmationPoller })._poller = p;
  }

  /** Internal: write a new SettlementAttempt (for queue) or a transition (for drive). */
  private record(
    base: Pick<SettlementAttempt, "receiptId" | "recipient" | "amountRaw" | "idempotencyKey" | "status" | "reason"> & {
      failureReason?: SettlementFailureReason;
    },
  ): SettlementAttempt {
    const now = new Date().toISOString();
    const draft: Omit<SettlementAttempt, "attemptHash"> = {
      id: randomUUID(),
      receiptId: base.receiptId,
      idempotencyKey: base.idempotencyKey,
      recipient: base.recipient,
      amountRaw: base.amountRaw,
      status: base.status,
      createdAt: now,
      updatedAt: now,
      attempts: 0,
      decisions: [
        { at: now, from: base.status, to: base.status, reason: base.reason ?? "queued" },
      ],
      reason: base.reason,
      failureReason: base.failureReason,
    };
    const rec: SettlementAttempt = { ...draft, attemptHash: hashAttempt(draft) };
    this.opts.journal.append(rec);
    this.opts.onTransition?.(rec);
    return rec;
  }

  private transition(
    cur: SettlementAttempt,
    to: SettlementStatus,
    patch: Partial<Pick<SettlementAttempt, "reason" | "txHash" | "confirmations" | "blockNumber" | "failureReason">> = {},
  ): SettlementAttempt {
    if (!canSettlementTransition(cur.status, to)) {
      throw new InvalidSettlementTransition(cur.status, to);
    }
    const now = new Date().toISOString();
    const attemptsDelta = to === "submitted" ? 1 : 0;
    const decisions = [
      ...cur.decisions,
      { at: now, from: cur.status, to, reason: patch.reason ?? cur.reason },
    ];
    const draft: Omit<SettlementAttempt, "attemptHash"> = {
      ...cur,
      ...patch,
      status: to,
      updatedAt: now,
      attempts: cur.attempts + attemptsDelta,
      decisions,
    };
    const rec: SettlementAttempt = { ...draft, attemptHash: hashAttempt(draft) };
    this.opts.journal.append(rec);
    this.opts.onTransition?.(rec);
    return rec;
  }
}

function mapSignerError(message: string): SettlementFailureReason {
  const m = classifyTxSendFailure(message);
  switch (m) {
    case "insufficient-balance":
      return "insufficient-balance";
    case "bad-chain-id":
      return "chain-mismatch";
    case "wallet-not-found":
      return "signing-failure";
    case "nonce-conflict":
      return "nonce-conflict";
    case "rpc-unavailable":
      return "rpc-unavailable";
    case "tx-rejected":
      return "signing-failure";
    case "tx-not-admitted":
      return "tx-dropped";
    case "tx-not-confirmed":
      return "confirmation-timeout";
    case "timeout":
      return "rpc-unavailable";
    default:
      return "unknown";
  }
}
