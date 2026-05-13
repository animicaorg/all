/**
 * Settlement reconciliation.
 *
 * The settlement engine already persists every state transition before it
 * is observable to the caller. This module is the durable recovery surface:
 *
 *   - `reconcilePending` walks the journal, finds every non-terminal attempt
 *     (`pending_submission`, `submitted`, `confirming`, `failed_transient`),
 *     and drives each one step. Read-only by default — a `pending_submission`
 *     attempt is **not** re-broadcast unless the caller passes a signer.
 *   - `inspectAttempt` returns the full decision-by-decision history for
 *     one receipt id, including a synthetic `broadcast_pending_confirmation`
 *     stage tag for any attempt that has a txHash but no terminal verdict.
 *   - `classifyReconcileOutcome` maps the engine's final status to an
 *     operator-readable verdict (paid / still-confirming / dropped / replaced
 *     / rejected / expired / failed / stuck-pending).
 *
 * Designed so an interrupted `submit-live` (process killed between
 * `pending_submission` and `submitted`, or between `submitted` and
 * `confirmed`) is fully resumable: re-run `settlement reconcile` and the
 * engine continues from durable state. Idempotency is provided by the
 * journal's stable `idempotencyKey = sha256(receiptId + artifactHash)`.
 */

import {
  BasicPreflight,
  RpcConfirmationPoller,
  SettlementEngine,
  SettlementJournal,
  SETTLEMENT_TERMINAL_STATES,
  type ConfirmationPoller,
  type SettlementAttempt,
  type SettlementFailureReason,
  type SettlementStatus,
} from "./settlement-engine.js";
import type { Signer } from "./wallet.js";

/**
 * Operator-visible classification of an in-flight attempt's current state.
 * `broadcast_pending_confirmation` is a virtual tag: it never appears in the
 * journal, but it's how we describe the durable `submitted` / `confirming`
 * states to operators who want to see "this tx hash exists on chain, we are
 * waiting for confirmations."
 */
export type ReconcileClassification =
  | "paid"
  | "broadcast_pending_confirmation"
  | "still-confirming"
  | "stuck-pending"
  | "dropped"
  | "replaced"
  | "rejected"
  | "expired"
  | "failed";

export function classifyReconcile(att: SettlementAttempt): ReconcileClassification {
  switch (att.status) {
    case "paid":
      return "paid";
    case "submitted":
    case "confirming":
      // We have a tx hash on chain but no terminal verdict yet.
      return att.txHash ? "broadcast_pending_confirmation" : "still-confirming";
    case "confirmed":
      return "still-confirming"; // engine will move to paid on next drive
    case "rejected":
      return "rejected";
    case "expired":
      return "expired";
    case "failed_permanent":
      return "failed";
    case "failed_transient":
      if (att.failureReason === "tx-dropped") return "dropped";
      if (att.failureReason === "tx-replaced") return "replaced";
      return "stuck-pending";
    case "pending_submission":
      return "stuck-pending";
  }
}

export interface ReconcileEntry {
  receiptId: string;
  before: SettlementStatus;
  after: SettlementStatus;
  txHash?: string;
  confirmations?: number;
  classification: ReconcileClassification;
  failureReason?: SettlementFailureReason;
  message?: string;
  attemptId: string;
  attempts: number;
  updatedAt: string;
}

export interface ReconcileOptions {
  stateDir: string;
  rpcUrl: string;
  confirmationDepth?: number;
  maxAttempts?: number;
  attemptDeadlineMs?: number;
  /** Restrict reconciliation to these receipt ids. Empty = all in-flight. */
  receiptIds?: string[];
  /** Optional signer. Without one, `pending_submission` attempts are surfaced
   *  as `stuck-pending` rather than re-broadcast. */
  signer?: Signer;
  /** Optional poller override (tests). */
  poller?: ConfirmationPoller;
  /** Optional onTransition observer hook (tests/dashboards). */
  onTransition?: (rec: SettlementAttempt) => void;
}

/**
 * Walks the journal and drives every non-terminal attempt one step forward.
 * Returns an entry per attempt for operator inspection. Safe to call
 * repeatedly — every step is journaled.
 */
export async function reconcilePending(opts: ReconcileOptions): Promise<ReconcileEntry[]> {
  const journal = new SettlementJournal(opts.stateDir);
  journal.reload();
  const all = journal.list();
  const targets =
    opts.receiptIds && opts.receiptIds.length > 0
      ? all.filter((a) => opts.receiptIds!.includes(a.receiptId))
      : all.filter((a) => !SETTLEMENT_TERMINAL_STATES.has(a.status));

  if (targets.length === 0) return [];

  const refusalSigner: Signer = {
    name: "reconcile-readonly",
    async sign() {
      throw new Error("reconcile: no signer supplied, refusing to re-broadcast pending_submission");
    },
  };
  const signer = opts.signer ?? refusalSigner;

  const engine = new SettlementEngine(
    {
      signer,
      journal,
      confirmationDepth: opts.confirmationDepth ?? 1,
      maxAttempts: opts.maxAttempts ?? 5,
      attemptDeadlineMs: opts.attemptDeadlineMs ?? 24 * 60 * 60 * 1000,
      confirmIntervalMs: 2000,
      confirmMaxPolls: 30,
      onTransition: opts.onTransition,
    },
    new BasicPreflight(),
  );
  engine.attachPoller(opts.poller ?? new RpcConfirmationPoller(opts.rpcUrl));

  const out: ReconcileEntry[] = [];
  for (const t of targets) {
    const before = t.status;
    let after: SettlementAttempt = t;
    let crashed: Error | null = null;
    // Drive forward until terminal-or-stall. We STOP iterating the moment
    // we *enter* `failed_transient` mid-pass so an operator can see the
    // specific transient failure reason (tx-dropped, tx-replaced, ...).
    // But if the attempt STARTED at failed_transient (operator retry after
    // supplying a signer), allow one recovery cycle so the engine can
    // transition failed_transient→pending_submission and try a fresh submit.
    let safety = 16;
    const startedAtFailedTransient = before === "failed_transient";
    let allowRecovery = startedAtFailedTransient;
    while (safety-- > 0 && !SETTLEMENT_TERMINAL_STATES.has(after.status)) {
      if (after.status === "failed_transient" && !allowRecovery) break;
      let next: SettlementAttempt;
      try {
        next = await engine.drive(t.receiptId);
      } catch (err) {
        crashed = err as Error;
        break;
      }
      if (next.status === after.status && next.id === after.id) break;
      // Recovery budget is one-shot: once we've left failed_transient, any
      // re-entry is a fresh transient failure that the operator should see.
      if (after.status === "failed_transient") allowRecovery = false;
      after = next;
    }
    if (crashed) {
      out.push({
        receiptId: t.receiptId,
        attemptId: after.id,
        before,
        after: after.status,
        txHash: after.txHash,
        confirmations: after.confirmations,
        classification: classifyReconcile(after),
        message: crashed.message?.slice(0, 200),
        attempts: after.attempts,
        updatedAt: after.updatedAt,
      });
      continue;
    }
    out.push({
      receiptId: t.receiptId,
      attemptId: after.id,
      before,
      after: after.status,
      txHash: after.txHash,
      confirmations: after.confirmations,
      classification: classifyReconcile(after),
      failureReason: after.failureReason,
      message: after.reason,
      attempts: after.attempts,
      updatedAt: after.updatedAt,
    });
  }
  return out;
}

export interface InspectHistoryEntry {
  at: string;
  from: SettlementStatus;
  to: SettlementStatus;
  reason?: string;
}

export interface InspectReport {
  receiptId: string;
  latest: SettlementAttempt;
  classification: ReconcileClassification;
  history: InspectHistoryEntry[];
  attempts: SettlementAttempt[];
}

/**
 * Returns full per-receipt history. Use for `settlement inspect <id>` so an
 * operator can see exactly which transitions fired and when.
 */
export function inspectAttempt(stateDir: string, receiptId: string): InspectReport | null {
  const journal = new SettlementJournal(stateDir);
  journal.reload();
  const all = journal.all(receiptId);
  if (all.length === 0) return null;
  const latest = all[all.length - 1];
  // Compose a flattened transition history across all attempts.
  const history: InspectHistoryEntry[] = [];
  for (const att of all) {
    for (const d of att.decisions) {
      history.push({ at: d.at, from: d.from, to: d.to, reason: d.reason });
    }
  }
  history.sort((a, b) => (a.at < b.at ? -1 : a.at > b.at ? 1 : 0));
  return {
    receiptId,
    latest,
    classification: classifyReconcile(latest),
    history,
    attempts: all,
  };
}

/** Filters the latest-per-receipt list to non-terminal states for `settlement pending`. */
export function listPending(stateDir: string): SettlementAttempt[] {
  const journal = new SettlementJournal(stateDir);
  journal.reload();
  return journal.list().filter((a) => !SETTLEMENT_TERMINAL_STATES.has(a.status));
}

/** Compact summary string for a reconcile run. */
export function summarizeReconcile(entries: ReconcileEntry[]): string {
  if (entries.length === 0) return "reconcile: no in-flight settlements";
  const counts = new Map<ReconcileClassification, number>();
  for (const e of entries) counts.set(e.classification, (counts.get(e.classification) ?? 0) + 1);
  const parts: string[] = [];
  for (const [k, v] of counts.entries()) parts.push(`${k}=${v}`);
  return `reconcile: ${entries.length} attempt(s); ${parts.join(" ")}`;
}
