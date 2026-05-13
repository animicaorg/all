/**
 * Operator-safe live settlement workflow.
 *
 * Wraps the existing `SettlementEngine` with three operator-visible phases:
 *
 *   1. verifyLive   — dry-run only. Reads chain state, validates signer,
 *                     prints/serializes the intended payload, refuses to
 *                     submit anything. Output is BigInt-safe.
 *   2. submitLive   — only proceeds when an explicit operator acknowledgement
 *                     is present. Persists an attempt before calling the
 *                     signer, then drives the state machine forward.
 *   3. watchLive    — resumes from the journal, drives all in-flight
 *                     attempts until terminal-or-stall, and reports a
 *                     summary classifying each (paid / confirming /
 *                     dropped / replaced / expired).
 *
 * The functions are designed to be called from the CLI but the core logic
 * has no CLI dependency so it can be unit-tested directly. Every action
 * preserves the SettlementEngine's `pending_submission → submitted →
 * confirming → confirmed → paid` ordering — no synthetic states.
 */

import type { AgentConfig } from "./config.js";
import type { BalanceLookup, BalanceProvider } from "./balance-provider.js";
import { RpcBalanceProvider } from "./balance-provider.js";
import { evaluatePayout, type PayoutPolicyConfig } from "./payout-policy.js";
import { checkCoordinatorFreshness } from "./coordinator-verify.js";
import type { MetricsRegistry } from "./metrics.js";
import { isLikelyAnimicaAddress } from "./rpc.js";
import {
  BasicPreflight,
  RpcConfirmationPoller,
  SettlementEngine,
  SettlementJournal,
  SETTLEMENT_TERMINAL_STATES,
  type ConfirmationPoller,
  type SettlementAttempt,
  type SettlementFailureReason,
  type SettlementInput,
  type SettlementStatus,
} from "./settlement-engine.js";
import { formatANM, type Signer } from "./wallet.js";

/** Stable opt-in string the operator must pass to authorize a live submission. */
export const LIVE_SUBMIT_ACK = "I-UNDERSTAND-THIS-SPENDS-REAL-FUNDS";

export interface LiveSettlementPayload {
  receiptId: string;
  recipient: string;
  amountRaw: bigint;
  artifactHash?: string;
  /** Human-formatted amount, for printing only. */
  formattedAmount: string;
}

export interface LiveVerifyReport {
  /** True if every check passed and submission would proceed. */
  ok: boolean;
  generatedAt: string;
  payload: LiveSettlementPayload;
  checks: {
    name: string;
    ok: boolean;
    level: "info" | "warning" | "error";
    message: string;
    detail?: Record<string, unknown>;
  }[];
  /** Concise operator summary. */
  summary: string;
  /** Risk acknowledgements the operator must read before submit-live. */
  risks: string[];
}

export interface LiveVerifyOptions {
  /** Where the settlement journal lives. */
  stateDir: string;
  /** Optional balance provider; defaults to RpcBalanceProvider from cfg. */
  balanceProvider?: BalanceProvider;
  /** Optional policy config to dry-run through the evaluator. */
  policy?: PayoutPolicyConfig;
  /** Optional signer address; falls back to cfg.minerAddress. */
  signerAddress?: string;
  /** Optional fetch shim (also used to construct the default balance provider). */
  fetchImpl?: typeof fetch;
}

/**
 * Read-only dry-run. Never calls the signer. Returns a structured report
 * with a hard `ok` boolean.
 */
export async function verifyLive(
  cfg: AgentConfig,
  payload: LiveSettlementPayload,
  opts: LiveVerifyOptions,
): Promise<LiveVerifyReport> {
  const checks: LiveVerifyReport["checks"] = [];
  const signerAddress = opts.signerAddress ?? cfg.minerAddress ?? "";

  // 1. Identity & recipient.
  checks.push({
    name: "signer-address",
    ok: !!signerAddress && isLikelyAnimicaAddress(signerAddress),
    level: signerAddress && isLikelyAnimicaAddress(signerAddress) ? "info" : "error",
    message: signerAddress
      ? isLikelyAnimicaAddress(signerAddress)
        ? `signer: ${signerAddress}`
        : `signer address failed prefix check: ${signerAddress}`
      : "no signer address (set minerAddress in config or via --signer)",
  });
  checks.push({
    name: "recipient",
    ok: isLikelyAnimicaAddress(payload.recipient),
    level: isLikelyAnimicaAddress(payload.recipient) ? "info" : "error",
    message: isLikelyAnimicaAddress(payload.recipient)
      ? `recipient: ${payload.recipient}`
      : `recipient address failed prefix check: ${payload.recipient}`,
  });

  // 2. Amount sanity.
  checks.push({
    name: "amount",
    ok: payload.amountRaw > 0n,
    level: payload.amountRaw > 0n ? "info" : "error",
    message: payload.amountRaw > 0n
      ? `amount: ${payload.formattedAmount} ANM (${payload.amountRaw.toString()} raw)`
      : "amount must be > 0",
  });

  // 3. RPC + chain id + balance — performed via the BalanceProvider so a
  //    single transport (and a single shim, in tests) covers chain id check
  //    AND balance retrieval. The provider returns an explicit failure for
  //    rpc-unreachable / chain-id-mismatch / malformed reply.
  const provider =
    opts.balanceProvider ??
    new RpcBalanceProvider({
      rpcUrl: cfg.rpcUrl,
      expectedChainId: cfg.chainId,
      fetchImpl: opts.fetchImpl,
    });
  let lookup: BalanceLookup | undefined;
  if (signerAddress) {
    lookup = await provider.lookup(signerAddress);
    checks.push({
      name: "balance-lookup",
      ok: lookup.ok,
      level: lookup.ok ? "info" : "error",
      message: lookup.ok
        ? `signer balance: ${lookup.balance.formattedANM} ANM (chainId=${lookup.observedChainId})`
        : `balance lookup failed (${lookup.failureReason}): ${lookup.message}`,
      detail: lookup.ok ? { raw: lookup.balance.raw.toString() } : undefined,
    });
    if (lookup.ok && lookup.balance.raw < payload.amountRaw) {
      checks.push({
        name: "balance-sufficient",
        ok: false,
        level: "error",
        message: `signer balance ${lookup.balance.formattedANM} ANM is below payout amount ${payload.formattedAmount} ANM`,
      });
    }
  }

  // 5. Policy dry-run (skipped if no policy provided).
  if (opts.policy) {
    const dryReceipt = {
      id: payload.receiptId,
      at: new Date(Date.now() - 5 * 60_000).toISOString(),
      kind: "scaffold" as const,
      estimate: {
        raw: payload.amountRaw,
        formattedANM: payload.formattedAmount,
        tier: "base",
        breakdown: [],
      },
      status: "estimated" as const,
      wallet: payload.recipient,
      receiptHash: "0".repeat(64),
      idempotencyKey: `useful-work:${payload.receiptId}:${payload.artifactHash ?? "no-artifact"}`,
    };
    const ev = evaluatePayout(
      {
        // The receipt is synthesized for dry-run only; the policy still
        // checks every axis (caps, maturity, artifact-hash mandate, …).
        receipt: dryReceipt as unknown as Parameters<typeof evaluatePayout>[0]["receipt"],
        recipient: payload.recipient,
        amountRaw: payload.amountRaw,
        artifactHash: payload.artifactHash,
        chainId: cfg.chainId,
        signerBalance: lookup?.ok ? lookup.balance : undefined,
      },
      opts.policy,
    );
    checks.push({
      name: "payout-policy",
      ok: ev.allowed,
      level: ev.allowed ? "info" : "error",
      message: ev.allowed ? "policy: allowed (dry-run)" : `policy: refused — ${ev.reason}: ${ev.message ?? ""}`,
    });
  }

  // 6. Journal idempotency check — refuse if a confirmed/paid attempt
  //    already exists for this receipt id.
  const journal = new SettlementJournal(opts.stateDir);
  journal.reload();
  const existing = journal.latest(payload.receiptId);
  if (existing) {
    const blocking = SETTLEMENT_TERMINAL_STATES.has(existing.status) || existing.status === "confirmed";
    checks.push({
      name: "journal-idempotency",
      ok: !blocking,
      level: blocking ? (existing.status === "paid" ? "error" : "warning") : "info",
      message: blocking
        ? `receipt ${payload.receiptId} already has an attempt in status=${existing.status} (tx=${existing.txHash ?? "—"})`
        : `prior non-terminal attempt found, status=${existing.status}`,
    });
  }

  const ok = checks.every((c) => c.ok || c.level !== "error");
  const failures = checks.filter((c) => !c.ok && c.level === "error");
  const summary = ok
    ? `verify-live: GO. ready to submit ${payload.formattedAmount} ANM to ${payload.recipient}`
    : `verify-live: NO-GO. ${failures.length} blocker(s)`;

  return {
    ok,
    generatedAt: new Date().toISOString(),
    payload,
    checks,
    summary,
    risks: [
      "submit-live will broadcast a real transaction signed by the configured signer",
      "this action is irreversible once the tx is admitted to the mempool",
      `it transfers ${payload.formattedAmount} ANM from the signer to ${payload.recipient}`,
      "settlement reaches `paid` only after the configured confirmation depth — re-organize windows can still flip the status",
    ],
  };
}

export interface LiveSubmitOptions extends LiveVerifyOptions {
  signer: Signer;
  poller?: ConfirmationPoller;
  /** Confirmation depth before the engine marks `paid`. */
  confirmationDepth?: number;
  /** Max attempts per receipt. */
  maxAttempts?: number;
  /** Hard deadline relative to attempt creation. */
  attemptDeadlineMs?: number;
  /** Acknowledgement token the operator must pass. Must equal LIVE_SUBMIT_ACK. */
  acknowledgement: string;
  /** When true, only writes the pending_submission record; does not call the signer. */
  persistOnly?: boolean;
  /** When set, refuses to broadcast unless a successful coordinator verify-live
   *  report exists within `coordinatorFreshnessWindowMs`. */
  requireFreshCoordinator?: { baseUrl?: string; windowMs?: number };
  /** Optional metrics registry to record live-payout counters. */
  metrics?: MetricsRegistry;
}

export class LiveSubmitRefused extends Error {
  constructor(
    public readonly reason: "no-ack" | "verify-failed" | "duplicate" | "policy" | "coordinator-stale",
    message: string,
    public readonly verifyReport?: LiveVerifyReport,
  ) {
    super(message);
    this.name = "LiveSubmitRefused";
  }
}

export interface LiveSubmitOutcome {
  attempt: SettlementAttempt;
  /** True if the engine ran a verify-live pass internally and it passed. */
  verifyPassed: boolean;
  /** Last verify-live report (always populated). */
  verifyReport: LiveVerifyReport;
}

/**
 * Runs verify-live, refuses without an explicit acknowledgement, persists
 * the attempt before calling the signer, then drives the engine forward
 * one full pass.
 */
export async function submitLive(
  cfg: AgentConfig,
  payload: LiveSettlementPayload,
  opts: LiveSubmitOptions,
): Promise<LiveSubmitOutcome> {
  if (opts.acknowledgement !== LIVE_SUBMIT_ACK) {
    const report = await verifyLive(cfg, payload, opts);
    throw new LiveSubmitRefused(
      "no-ack",
      `live submit refused: missing operator acknowledgement. Pass acknowledgement="${LIVE_SUBMIT_ACK}".`,
      report,
    );
  }
  if (opts.requireFreshCoordinator) {
    const fresh = checkCoordinatorFreshness({
      stateDir: opts.stateDir,
      baseUrl: opts.requireFreshCoordinator.baseUrl,
      windowMs: opts.requireFreshCoordinator.windowMs,
    });
    if (!fresh.fresh) {
      const report = await verifyLive(cfg, payload, opts);
      throw new LiveSubmitRefused(
        "coordinator-stale",
        `live submit refused: coordinator verification is ${fresh.reason ?? "stale"}; run 'animica-agent coordinator verify-live --url <url>' first`,
        report,
      );
    }
  }
  const verifyReport = await verifyLive(cfg, payload, opts);
  if (!verifyReport.ok) {
    throw new LiveSubmitRefused(
      "verify-failed",
      `live submit refused: verify-live reports ${verifyReport.checks.filter((c) => !c.ok && c.level === "error").length} blocker(s)`,
      verifyReport,
    );
  }
  const journal = new SettlementJournal(opts.stateDir);
  const metrics = opts.metrics;
  const engine = new SettlementEngine(
    {
      signer: opts.signer,
      journal,
      confirmationDepth: opts.confirmationDepth ?? 1,
      maxAttempts: opts.maxAttempts ?? 5,
      attemptDeadlineMs: opts.attemptDeadlineMs ?? 24 * 60 * 60 * 1000,
      confirmIntervalMs: 2000,
      confirmMaxPolls: 30,
      onTransition: metrics
        ? (rec) => {
            // Count meaningful broadcast / confirmation / rejection events.
            if (rec.status === "submitted") metrics.inc("payouts_broadcast");
            else if (rec.status === "paid") metrics.inc("payouts_confirmed");
            else if (rec.status === "rejected") {
              metrics.inc("payouts_rejected");
              metrics.inc("settlement_rejects");
            }
          }
        : undefined,
    },
    new BasicPreflight(),
  );
  const poller = opts.poller ?? new RpcConfirmationPoller(cfg.rpcUrl);
  engine.attachPoller(poller);

  const input: SettlementInput = {
    receiptId: payload.receiptId,
    recipient: payload.recipient,
    amountRaw: payload.amountRaw,
    artifactHash: payload.artifactHash,
    config: cfg,
  };
  const queued = await engine.queue(input);
  // Refuse if an authoritative prior attempt already exists.
  if (
    queued.id !== queued.id ||
    queued.status === "paid" ||
    queued.status === "confirmed" ||
    queued.status === "rejected"
  ) {
    throw new LiveSubmitRefused(
      "duplicate",
      `live submit refused: attempt already exists for receipt ${payload.receiptId} in status=${queued.status}`,
      verifyReport,
    );
  }
  if (opts.persistOnly) {
    return { attempt: queued, verifyPassed: true, verifyReport };
  }
  const driven = await engine.settleOnce(input);
  return { attempt: driven, verifyPassed: true, verifyReport };
}

export interface WatchOptions {
  stateDir: string;
  rpcUrl: string;
  /** Confirmation depth. Defaults to 1. */
  confirmationDepth?: number;
  /** Filter to a subset of receiptIds. Empty = all in-flight. */
  receiptIds?: string[];
  /** Optional signer; required only when an attempt is `pending_submission`
   *  and we want to re-broadcast. Default behavior is read-only watching. */
  signer?: Signer;
  /** Optional poller override. */
  poller?: ConfirmationPoller;
}

export interface WatchEntry {
  receiptId: string;
  before: SettlementStatus;
  after: SettlementStatus;
  txHash?: string;
  confirmations?: number;
  classification: WatchClassification;
  failureReason?: SettlementFailureReason;
  message?: string;
}

export type WatchClassification =
  | "paid"
  | "still-confirming"
  | "stuck-pending"
  | "dropped"
  | "replaced"
  | "rejected"
  | "expired"
  | "failed";

export function classifyWatchOutcome(before: SettlementStatus, after: SettlementAttempt): WatchClassification {
  if (after.status === "paid") return "paid";
  if (after.status === "rejected") return "rejected";
  if (after.status === "expired") return "expired";
  if (after.status === "failed_permanent") return "failed";
  if (after.status === "failed_transient") {
    if (after.failureReason === "tx-dropped") return "dropped";
    if (after.failureReason === "tx-replaced") return "replaced";
    return "stuck-pending";
  }
  if (after.status === "confirming" || after.status === "submitted") return "still-confirming";
  if (after.status === "pending_submission") return "stuck-pending";
  return "still-confirming";
}

/**
 * Walks the journal and drives each non-terminal attempt one step. Read-only
 * when no signer is supplied — only the poller is consulted, so a stuck
 * `pending_submission` will not be re-broadcast unless the caller passes a
 * signer.
 */
export async function watchLive(opts: WatchOptions): Promise<WatchEntry[]> {
  const journal = new SettlementJournal(opts.stateDir);
  journal.reload();
  const all = journal.list();
  const targets =
    opts.receiptIds && opts.receiptIds.length > 0
      ? all.filter((a) => opts.receiptIds!.includes(a.receiptId))
      : all.filter((a) => !SETTLEMENT_TERMINAL_STATES.has(a.status));

  if (targets.length === 0) return [];

  // The engine refuses to advance pending_submission without a signer. We
  // pass a refusing signer for read-only mode so the engine never broadcasts
  // anything unexpected.
  const signer: Signer = opts.signer ?? {
    name: "watch-readonly",
    async sign() {
      throw new Error("watchLive: no signer supplied, cannot re-broadcast pending_submission");
    },
  };
  const engine = new SettlementEngine(
    {
      signer,
      journal,
      confirmationDepth: opts.confirmationDepth ?? 1,
      maxAttempts: 5,
      attemptDeadlineMs: 24 * 60 * 60 * 1000,
      confirmIntervalMs: 2000,
      confirmMaxPolls: 30,
    },
    new BasicPreflight(),
  );
  engine.attachPoller(opts.poller ?? new RpcConfirmationPoller(opts.rpcUrl));

  const out: WatchEntry[] = [];
  for (const t of targets) {
    const before = t.status;
    let after: SettlementAttempt;
    try {
      after = await engine.drive(t.receiptId);
    } catch (err) {
      out.push({
        receiptId: t.receiptId,
        before,
        after: before,
        classification: "stuck-pending",
        message: (err as Error).message?.slice(0, 200),
      });
      continue;
    }
    out.push({
      receiptId: t.receiptId,
      before,
      after: after.status,
      txHash: after.txHash,
      confirmations: after.confirmations,
      classification: classifyWatchOutcome(before, after),
      failureReason: after.failureReason,
      message: after.reason,
    });
  }
  return out;
}

/**
 * Convenience: build a printable summary of a watch run.
 */
export function summarizeWatch(entries: WatchEntry[]): string {
  if (entries.length === 0) return "watch-live: no in-flight settlements";
  const counts = new Map<WatchClassification, number>();
  for (const e of entries) counts.set(e.classification, (counts.get(e.classification) ?? 0) + 1);
  const parts: string[] = [];
  for (const [k, v] of counts.entries()) parts.push(`${k}=${v}`);
  return `watch-live: ${entries.length} attempt(s); ${parts.join(" ")}`;
}

/**
 * Build a default `LiveSettlementPayload` from a settled receipt + signer
 * address. Helpful for the CLI when the operator gives us only a receiptId.
 */
export function payloadFromReceipt(
  receiptId: string,
  recipient: string,
  amountRaw: bigint,
  artifactHash?: string,
): LiveSettlementPayload {
  return {
    receiptId,
    recipient,
    amountRaw,
    artifactHash,
    formattedAmount: formatANM(amountRaw),
  };
}
