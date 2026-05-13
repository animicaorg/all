/**
 * Operator-facing rollups across the useful-work pipeline.
 *
 * Combines three persisted sources:
 *   - JobStateStore  (jobs.jsonl)             — job lifecycle states
 *   - BillingEngine  (receipts.jsonl)         — settlement receipts
 *   - UsageJournal   (usage.jsonl)            — usage attribution
 *
 * Every aggregator returns plain, BigInt-safe shapes — never the raw
 * persisted records — so they can be JSON-rendered by the CLI without
 * surprises.
 */

import { BillingEngine, type Receipt } from "./billing.js";
import { formatANM } from "./wallet.js";
import type { AgentConfig } from "./config.js";
import { JobStateStore, TERMINAL_STATUSES, type JobStateRecord, type JobStatus } from "./job-state.js";

export interface JobAggregate {
  /** Status → count of jobs in that status. */
  byStatus: Record<JobStatus, number>;
  /** Total non-terminal in-flight count. */
  inFlight: number;
  /** Total terminal count. */
  terminal: number;
  /** Most-recent transition across the whole set. */
  lastUpdatedAt?: string;
}

export interface AddressAggregate {
  address: string;
  workers: string[];
  jobsTotal: number;
  jobsTerminal: number;
  jobsPaid: number;
  rewardsRaw: bigint;
  rewardsFormatted: string;
  receipts: { settled: number; failed: number; rejected: number; pending: number };
  /** Latest receipt timestamp seen for this address. */
  lastReceiptAt?: string;
}

export interface WorkerAggregate {
  worker: string;
  addresses: string[];
  jobsTotal: number;
  jobsTerminal: number;
  jobsPaid: number;
  rewardsRaw: bigint;
  rewardsFormatted: string;
}

export interface SettlementReadyView {
  /** Jobs whose state is artifacted or accepted_remote and whose receipt is not yet settled. */
  ready: { jobId: string; receiptId?: string; minerAddress?: string; worker?: string; estimatedRewardRaw: bigint; estimatedRewardFormatted: string }[];
  /** Receipts persisted but not yet settled. */
  pendingReceipts: { id: string; wallet?: string; worker?: string; raw: bigint; formatted: string; at: string }[];
  totalRaw: bigint;
  totalFormatted: string;
}

const ZERO_BY_STATUS = (): Record<JobStatus, number> => ({
  discovered: 0,
  accepted: 0,
  running: 0,
  completed: 0,
  artifacted: 0,
  submitted: 0,
  accepted_remote: 0,
  rejected_remote: 0,
  settlement_pending: 0,
  paid: 0,
  failed: 0,
});

export function aggregateJobs(records: JobStateRecord[]): JobAggregate {
  const byStatus = ZERO_BY_STATUS();
  let inFlight = 0;
  let terminal = 0;
  let lastUpdatedAt: string | undefined;
  for (const r of records) {
    byStatus[r.status] = (byStatus[r.status] ?? 0) + 1;
    if (TERMINAL_STATUSES.has(r.status)) terminal++;
    else inFlight++;
    if (!lastUpdatedAt || r.updatedAt > lastUpdatedAt) lastUpdatedAt = r.updatedAt;
  }
  return { byStatus, inFlight, terminal, lastUpdatedAt };
}

export function aggregateByAddress(records: JobStateRecord[], receipts: Receipt[]): AddressAggregate[] {
  const byAddr = new Map<string, AddressAggregate>();
  for (const r of records) {
    const addr = r.minerAddress ?? "(unknown)";
    let agg = byAddr.get(addr);
    if (!agg) {
      agg = {
        address: addr,
        workers: [],
        jobsTotal: 0,
        jobsTerminal: 0,
        jobsPaid: 0,
        rewardsRaw: 0n,
        rewardsFormatted: "0",
        receipts: { settled: 0, failed: 0, rejected: 0, pending: 0 },
      };
      byAddr.set(addr, agg);
    }
    agg.jobsTotal++;
    if (TERMINAL_STATUSES.has(r.status)) agg.jobsTerminal++;
    if (r.status === "paid") agg.jobsPaid++;
    if (r.workerName && !agg.workers.includes(r.workerName)) agg.workers.push(r.workerName);
  }
  for (const rcpt of receipts) {
    const addr = rcpt.wallet ?? "(unknown)";
    let agg = byAddr.get(addr);
    if (!agg) {
      // Receipt exists for an address we haven't seen in the job store; still surface it.
      agg = {
        address: addr,
        workers: rcpt.worker ? [rcpt.worker] : [],
        jobsTotal: 0,
        jobsTerminal: 0,
        jobsPaid: 0,
        rewardsRaw: 0n,
        rewardsFormatted: "0",
        receipts: { settled: 0, failed: 0, rejected: 0, pending: 0 },
      };
      byAddr.set(addr, agg);
    }
    if (rcpt.worker && !agg.workers.includes(rcpt.worker)) agg.workers.push(rcpt.worker);
    bumpReceiptBucket(agg.receipts, rcpt.status);
    if (rcpt.status === "settled") {
      agg.rewardsRaw += rcpt.actualCostRaw ?? rcpt.estimate.raw;
    }
    if (!agg.lastReceiptAt || rcpt.at > agg.lastReceiptAt) agg.lastReceiptAt = rcpt.at;
  }
  for (const agg of byAddr.values()) agg.rewardsFormatted = formatANM(agg.rewardsRaw);
  return [...byAddr.values()].sort((a, b) => (a.rewardsRaw > b.rewardsRaw ? -1 : a.rewardsRaw < b.rewardsRaw ? 1 : 0));
}

export function aggregateByWorker(records: JobStateRecord[], receipts: Receipt[]): WorkerAggregate[] {
  const byWorker = new Map<string, WorkerAggregate>();
  for (const r of records) {
    const w = r.workerName ?? "(unknown)";
    let agg = byWorker.get(w);
    if (!agg) {
      agg = {
        worker: w,
        addresses: [],
        jobsTotal: 0,
        jobsTerminal: 0,
        jobsPaid: 0,
        rewardsRaw: 0n,
        rewardsFormatted: "0",
      };
      byWorker.set(w, agg);
    }
    agg.jobsTotal++;
    if (TERMINAL_STATUSES.has(r.status)) agg.jobsTerminal++;
    if (r.status === "paid") agg.jobsPaid++;
    if (r.minerAddress && !agg.addresses.includes(r.minerAddress)) agg.addresses.push(r.minerAddress);
  }
  for (const rcpt of receipts) {
    const w = rcpt.worker ?? "(unknown)";
    let agg = byWorker.get(w);
    if (!agg) {
      agg = {
        worker: w,
        addresses: rcpt.wallet ? [rcpt.wallet] : [],
        jobsTotal: 0,
        jobsTerminal: 0,
        jobsPaid: 0,
        rewardsRaw: 0n,
        rewardsFormatted: "0",
      };
      byWorker.set(w, agg);
    }
    if (rcpt.wallet && !agg.addresses.includes(rcpt.wallet)) agg.addresses.push(rcpt.wallet);
    if (rcpt.status === "settled") {
      agg.rewardsRaw += rcpt.actualCostRaw ?? rcpt.estimate.raw;
    }
  }
  for (const agg of byWorker.values()) agg.rewardsFormatted = formatANM(agg.rewardsRaw);
  return [...byWorker.values()].sort((a, b) => (a.rewardsRaw > b.rewardsRaw ? -1 : a.rewardsRaw < b.rewardsRaw ? 1 : 0));
}

function bumpReceiptBucket(buckets: AddressAggregate["receipts"], status: Receipt["status"]): void {
  if (status === "settled") buckets.settled++;
  else if (status === "failed") buckets.failed++;
  else if (status === "rejected") buckets.rejected++;
  else buckets.pending++;
}

export function settlementReady(records: JobStateRecord[], receipts: Receipt[]): SettlementReadyView {
  // A job is settlement-ready if it has reached settlement_pending OR
  // accepted_remote and we have a corresponding receipt that has not been
  // settled. We use the receipt's idempotencyKey vs jobId match.
  const receiptByJobId = new Map<string, Receipt>();
  for (const r of receipts) {
    if (r.idempotencyKey?.startsWith("useful-work:")) {
      const jobId = r.idempotencyKey.split(":")[1];
      if (jobId) receiptByJobId.set(jobId, r);
    }
  }
  const ready: SettlementReadyView["ready"] = [];
  const pendingReceipts: SettlementReadyView["pendingReceipts"] = [];
  let totalRaw = 0n;
  for (const rec of records) {
    if (rec.status !== "accepted_remote" && rec.status !== "settlement_pending") continue;
    const r = rec.receiptId ? receipts.find((x) => x.id === rec.receiptId) : receiptByJobId.get(rec.jobId);
    if (!r) continue;
    if (r.status === "settled") continue;
    const raw = r.actualCostRaw ?? r.estimate.raw;
    ready.push({
      jobId: rec.jobId,
      receiptId: r.id,
      minerAddress: rec.minerAddress,
      worker: rec.workerName,
      estimatedRewardRaw: raw,
      estimatedRewardFormatted: formatANM(raw),
    });
    totalRaw += raw;
  }
  for (const r of receipts) {
    if (r.status === "settled") continue;
    const raw = r.actualCostRaw ?? r.estimate.raw;
    pendingReceipts.push({
      id: r.id,
      wallet: r.wallet,
      worker: r.worker,
      raw,
      formatted: formatANM(raw),
      at: r.at,
    });
  }
  return { ready, pendingReceipts, totalRaw, totalFormatted: formatANM(totalRaw) };
}

/** Convenience that wires up everything from disk given a state dir. */
export function buildRollup(stateDir: string, cfg: AgentConfig): {
  jobs: JobAggregate;
  byAddress: AddressAggregate[];
  byWorker: WorkerAggregate[];
  settlement: SettlementReadyView;
} {
  const store = new JobStateStore(stateDir);
  store.reload();
  const jobs = store.list();
  const billing = new BillingEngine(stateDir, cfg);
  const receipts = billing.listReceipts(10_000);
  return {
    jobs: aggregateJobs(jobs),
    byAddress: aggregateByAddress(jobs, receipts),
    byWorker: aggregateByWorker(jobs, receipts),
    settlement: settlementReady(jobs, receipts),
  };
}
