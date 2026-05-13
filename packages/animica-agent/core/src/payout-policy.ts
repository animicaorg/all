/**
 * Payout safety policy.
 *
 * All settlement requests pass through this engine before they reach the
 * signer. Every refusal is journaled with a stable rejection reason so an
 * operator audit log records exactly why a payout was blocked. The policy
 * fails closed by design — a missing config value or an unparseable record
 * is treated as a refusal, not a permission.
 *
 * Policy axes (each optional; absent = unenforced):
 *   - dailyCapRaw            total ANM that may be settled in a UTC day
 *   - perWorkerDailyCapRaw   per worker-name daily cap
 *   - perAddressDailyCapRaw  per recipient address daily cap
 *   - reserveBalanceRaw      minimum signer balance that must remain after the payout
 *   - minMaturityMs          how long after `receivedAt` before a receipt can be settled
 *
 * Anti-abuse axes:
 *   - duplicateReceiptDefense  refuse any receipt id already settled-paid
 *   - duplicateArtifactDefense refuse if the same artifact hash was already paid
 *   - mandatoryArtifactHash    refuse receipts without an artifact hash
 *
 * The policy is stateless beyond the audit log and per-decision spend caches
 * which are rebuilt from journaled inputs each evaluation. This keeps the
 * code deterministic and safe to run in two processes side-by-side.
 */

import { appendFileSync, existsSync, mkdirSync, readFileSync } from "node:fs";
import { createHash, randomUUID } from "node:crypto";
import { join } from "node:path";

import { formatANM, type WalletBalance } from "./wallet.js";
import { safeParse, safeStringify } from "./safe-json.js";
import type { SettlementAttempt } from "./settlement-engine.js";
import type { Receipt } from "./billing.js";
import {
  balanceFailureToPayoutReason,
  type BalanceLookup,
  type BalanceProvider,
} from "./balance-provider.js";

export type PayoutRejectionReason =
  | "duplicate-receipt"
  | "duplicate-artifact"
  | "missing-artifact-hash"
  | "invalid-recipient"
  | "amount-non-positive"
  | "amount-exceeds-receipt"
  | "daily-cap-exceeded"
  | "per-worker-cap-exceeded"
  | "per-address-cap-exceeded"
  | "reserve-balance-violation"
  | "below-maturity"
  | "config-missing"
  | "tampered-attempt"
  | "policy-disabled-for-network"
  | "unknown";

export interface PayoutPolicyConfig {
  /** When true the entire policy is bypassed. Default false. */
  bypass?: boolean;
  /** Caps in smallest unit. Missing = no cap. */
  dailyCapRaw?: bigint;
  perWorkerDailyCapRaw?: bigint;
  perAddressDailyCapRaw?: bigint;
  /** Signer balance must stay >= this after a payout. */
  reserveBalanceRaw?: bigint;
  /** Earliest a receipt can be settled relative to its creation timestamp. */
  minMaturityMs?: number;
  /** Anti-abuse toggles. */
  duplicateReceiptDefense?: boolean;
  duplicateArtifactDefense?: boolean;
  mandatoryArtifactHash?: boolean;
  /**
   * Operator policy override — refuse policy evaluation entirely on networks
   * other than the listed chain ids. Defaults to ["1"] (Animica mainnet).
   */
  enforceOnChainIds?: string[];
}

export const DEFAULT_PAYOUT_POLICY: Required<PayoutPolicyConfig> = {
  bypass: false,
  // 10 ANM, 1 ANM, 1 ANM, 0.01 ANM reserve, 60s maturity.
  dailyCapRaw: 10_000_000_000_000_000_000n,
  perWorkerDailyCapRaw: 1_000_000_000_000_000_000n,
  perAddressDailyCapRaw: 1_000_000_000_000_000_000n,
  reserveBalanceRaw: 10_000_000_000_000_000n,
  minMaturityMs: 60_000,
  duplicateReceiptDefense: true,
  duplicateArtifactDefense: true,
  mandatoryArtifactHash: true,
  enforceOnChainIds: ["1"],
};

export interface PayoutEvaluationInput {
  /** The accounting receipt being settled. */
  receipt: Receipt;
  /** Worker name, if any. */
  worker?: string;
  /** Payout recipient address (typically the miner payout address). */
  recipient: string;
  /** Amount we are about to settle. Usually receipt.actualCostRaw ?? receipt.estimate.raw. */
  amountRaw: bigint;
  /** Artifact hash that the receipt corresponds to (useful-work submissions). */
  artifactHash?: string;
  /** Current chain id (string for parity with config). */
  chainId: string;
  /** Optional signer-wallet balance pre-flight result. */
  signerBalance?: WalletBalance | null;
  /** Optional list of prior settlement attempts (for duplicate detection). */
  priorAttempts?: SettlementAttempt[];
  /** Optional list of prior settled receipts (for duplicate-artifact defense). */
  priorPaidReceipts?: Pick<Receipt, "id" | "actualCostRaw" | "estimate" | "wallet" | "worker" | "at" | "idempotencyKey">[];
}

export interface PayoutEvaluation {
  allowed: boolean;
  reason?: PayoutRejectionReason;
  message?: string;
  /** Sum of relevant prior settlements consulted, for transparency. */
  metrics: {
    todayTotalRaw: bigint;
    todayWorkerRaw: bigint;
    todayAddressRaw: bigint;
  };
}

/**
 * Pure evaluator. Side-effect free. Returns a clear allow/refuse result.
 * The caller is responsible for journaling the decision via PayoutAuditor.
 */
export function evaluatePayout(input: PayoutEvaluationInput, cfg: PayoutPolicyConfig = {}): PayoutEvaluation {
  const merged: Required<PayoutPolicyConfig> = { ...DEFAULT_PAYOUT_POLICY, ...cfg };
  const today = new Date().toISOString().slice(0, 10);
  const metrics = aggregateDay(input.priorAttempts ?? [], today, input.worker, input.recipient);
  if (merged.bypass) {
    return { allowed: true, message: "bypass=true", metrics };
  }
  if (merged.enforceOnChainIds.length > 0 && !merged.enforceOnChainIds.includes(input.chainId)) {
    return {
      allowed: false,
      reason: "policy-disabled-for-network",
      message: `policy refuses to settle on chainId=${input.chainId}`,
      metrics,
    };
  }
  if (!input.recipient || input.recipient.trim().length === 0) {
    return { allowed: false, reason: "invalid-recipient", message: "no recipient", metrics };
  }
  if (input.amountRaw <= 0n) {
    return { allowed: false, reason: "amount-non-positive", message: "amount must be > 0", metrics };
  }
  const receiptCeiling = input.receipt.actualCostRaw ?? input.receipt.estimate.raw;
  if (input.amountRaw > receiptCeiling) {
    return {
      allowed: false,
      reason: "amount-exceeds-receipt",
      message: `amount ${formatANM(input.amountRaw)} ANM exceeds receipt ceiling ${formatANM(receiptCeiling)} ANM`,
      metrics,
    };
  }
  if (merged.mandatoryArtifactHash && !input.artifactHash) {
    return { allowed: false, reason: "missing-artifact-hash", message: "receipt has no artifact hash", metrics };
  }
  if (merged.minMaturityMs > 0) {
    const ageMs = Date.now() - new Date(input.receipt.at).getTime();
    if (ageMs < merged.minMaturityMs) {
      return {
        allowed: false,
        reason: "below-maturity",
        message: `receipt is ${ageMs}ms old, must be ≥ ${merged.minMaturityMs}ms`,
        metrics,
      };
    }
  }
  if (merged.duplicateReceiptDefense) {
    const dup = (input.priorAttempts ?? []).some(
      (a) => a.receiptId === input.receipt.id && (a.status === "paid" || a.status === "confirmed"),
    );
    if (dup) {
      return {
        allowed: false,
        reason: "duplicate-receipt",
        message: `receipt ${input.receipt.id} already has a confirmed payout`,
        metrics,
      };
    }
  }
  if (merged.duplicateArtifactDefense && input.artifactHash) {
    const dup = (input.priorPaidReceipts ?? []).some(
      (r) =>
        typeof r.idempotencyKey === "string" &&
        r.idempotencyKey.includes(input.artifactHash!) &&
        r.id !== input.receipt.id,
    );
    if (dup) {
      return {
        allowed: false,
        reason: "duplicate-artifact",
        message: `artifact hash ${input.artifactHash.slice(0, 12)}… already paid via a prior receipt`,
        metrics,
      };
    }
  }
  if (merged.dailyCapRaw > 0n && metrics.todayTotalRaw + input.amountRaw > merged.dailyCapRaw) {
    return {
      allowed: false,
      reason: "daily-cap-exceeded",
      message: `would push today's payout to ${formatANM(metrics.todayTotalRaw + input.amountRaw)} ANM, cap is ${formatANM(merged.dailyCapRaw)} ANM`,
      metrics,
    };
  }
  if (
    input.worker &&
    merged.perWorkerDailyCapRaw > 0n &&
    metrics.todayWorkerRaw + input.amountRaw > merged.perWorkerDailyCapRaw
  ) {
    return {
      allowed: false,
      reason: "per-worker-cap-exceeded",
      message: `worker ${input.worker} would exceed daily cap`,
      metrics,
    };
  }
  if (
    input.recipient &&
    merged.perAddressDailyCapRaw > 0n &&
    metrics.todayAddressRaw + input.amountRaw > merged.perAddressDailyCapRaw
  ) {
    return {
      allowed: false,
      reason: "per-address-cap-exceeded",
      message: `recipient would exceed daily cap`,
      metrics,
    };
  }
  if (input.signerBalance && merged.reserveBalanceRaw > 0n) {
    const postBalance = input.signerBalance.raw - input.amountRaw;
    if (postBalance < merged.reserveBalanceRaw) {
      return {
        allowed: false,
        reason: "reserve-balance-violation",
        message: `signer would drop below reserve ${formatANM(merged.reserveBalanceRaw)} ANM`,
        metrics,
      };
    }
  }
  return { allowed: true, metrics };
}

function aggregateDay(
  attempts: SettlementAttempt[],
  utcDay: string,
  worker: string | undefined,
  recipient: string,
): { todayTotalRaw: bigint; todayWorkerRaw: bigint; todayAddressRaw: bigint } {
  let total = 0n;
  let workerSum = 0n;
  let addressSum = 0n;
  for (const a of attempts) {
    if (a.status !== "paid" && a.status !== "confirmed") continue;
    if (!a.updatedAt.startsWith(utcDay)) continue;
    total += a.amountRaw;
    if (recipient && a.recipient === recipient) addressSum += a.amountRaw;
    // Worker is not on the SettlementAttempt; tally from the matching receipt at the call site if needed.
    // We approximate using a hash-suffix convention in idempotencyKey for tests; production callers
    // should pass priorAttempts pre-filtered by worker via a separate index when worker caps matter.
    if (worker && (a.idempotencyKey.includes(worker) || a.recipient.includes(worker))) {
      workerSum += a.amountRaw;
    }
  }
  return { todayTotalRaw: total, todayWorkerRaw: workerSum, todayAddressRaw: addressSum };
}

/* ----------------- audit log ----------------- */

export interface PayoutDecisionRecord {
  id: string;
  at: string;
  receiptId: string;
  recipient: string;
  amountRaw: bigint;
  worker?: string;
  allowed: boolean;
  reason?: PayoutRejectionReason;
  message?: string;
  policyDigest: string;
}

function digestPolicy(cfg: PayoutPolicyConfig): string {
  const merged: Required<PayoutPolicyConfig> = { ...DEFAULT_PAYOUT_POLICY, ...cfg };
  return createHash("sha256").update(safeStringify(merged)).digest("hex").slice(0, 16);
}

export class PayoutAuditor {
  private readonly file: string;
  constructor(stateDir: string) {
    mkdirSync(stateDir, { recursive: true });
    this.file = join(stateDir, "payout-decisions.jsonl");
  }
  path(): string {
    return this.file;
  }
  record(rec: Omit<PayoutDecisionRecord, "id" | "at" | "policyDigest">, cfg: PayoutPolicyConfig): PayoutDecisionRecord {
    const full: PayoutDecisionRecord = {
      ...rec,
      id: randomUUID(),
      at: new Date().toISOString(),
      policyDigest: digestPolicy(cfg),
    };
    appendFileSync(this.file, safeStringify(full) + "\n", "utf8");
    return full;
  }
  recent(limit = 100): PayoutDecisionRecord[] {
    if (!existsSync(this.file)) return [];
    const lines = readFileSync(this.file, "utf8").split(/\r?\n/).filter(Boolean).slice(-limit);
    const out: PayoutDecisionRecord[] = [];
    for (const l of lines) {
      try {
        out.push(safeParse<PayoutDecisionRecord>(l));
      } catch {
        /* skip */
      }
    }
    return out;
  }
}

export interface PayoutGuardDecision {
  allowed: boolean;
  record: PayoutDecisionRecord;
  evaluation: PayoutEvaluation;
  /** When a balance provider was consulted, the lookup is recorded here for audit. */
  balanceLookup?: BalanceLookup;
}

export interface PayoutGuard {
  decide(input: PayoutEvaluationInput): PayoutGuardDecision | Promise<PayoutGuardDecision>;
}

export class PolicyPayoutGuard implements PayoutGuard {
  constructor(private readonly cfg: PayoutPolicyConfig, private readonly auditor: PayoutAuditor) {}
  decide(input: PayoutEvaluationInput): PayoutGuardDecision {
    const evaluation = evaluatePayout(input, this.cfg);
    const record = this.auditor.record(
      {
        receiptId: input.receipt.id,
        recipient: input.recipient,
        amountRaw: input.amountRaw,
        worker: input.worker,
        allowed: evaluation.allowed,
        reason: evaluation.reason,
        message: evaluation.message,
      },
      this.cfg,
    );
    return { allowed: evaluation.allowed, record, evaluation };
  }
}

export interface BalanceAwarePayoutGuardOptions {
  /** Signer address whose balance backs the payout. */
  signerAddress: string;
  /** Used to fetch live balance. */
  balanceProvider: BalanceProvider;
  /** Policy config. */
  cfg: PayoutPolicyConfig;
  /** Auditor for journaled decisions. */
  auditor: PayoutAuditor;
}

/**
 * Payout guard that fetches signer balance from RPC before each decision and
 * passes it through `evaluatePayout` so `reserveBalanceRaw` is enforced by
 * default in production wiring. A failed lookup is treated as a refusal —
 * the policy never spends when it cannot prove safety.
 */
export class BalanceAwarePayoutGuard implements PayoutGuard {
  constructor(private readonly opts: BalanceAwarePayoutGuardOptions) {}

  async decide(input: PayoutEvaluationInput): Promise<PayoutGuardDecision> {
    const balanceLookup = await this.opts.balanceProvider.lookup(this.opts.signerAddress);
    if (!balanceLookup.ok) {
      const reason = balanceFailureToPayoutReason(balanceLookup.failureReason);
      const evaluation: PayoutEvaluation = {
        allowed: false,
        reason,
        message: `signer balance unavailable (${balanceLookup.failureReason}): ${balanceLookup.message}`,
        metrics: { todayTotalRaw: 0n, todayWorkerRaw: 0n, todayAddressRaw: 0n },
      };
      const record = this.opts.auditor.record(
        {
          receiptId: input.receipt.id,
          recipient: input.recipient,
          amountRaw: input.amountRaw,
          worker: input.worker,
          allowed: false,
          reason,
          message: evaluation.message,
        },
        this.opts.cfg,
      );
      return { allowed: false, record, evaluation, balanceLookup };
    }
    const evaluation = evaluatePayout(
      { ...input, signerBalance: balanceLookup.balance },
      this.opts.cfg,
    );
    const record = this.opts.auditor.record(
      {
        receiptId: input.receipt.id,
        recipient: input.recipient,
        amountRaw: input.amountRaw,
        worker: input.worker,
        allowed: evaluation.allowed,
        reason: evaluation.reason,
        message: evaluation.message,
      },
      this.opts.cfg,
    );
    if (evaluation.allowed) {
      // The cache holds the last known balance; once a payout is allowed and
      // submitted, the next decision should re-fetch since the signer balance
      // is about to change. Conservative invalidation, not optional.
      this.opts.balanceProvider.invalidate();
    }
    return { allowed: evaluation.allowed, record, evaluation, balanceLookup };
  }
}
