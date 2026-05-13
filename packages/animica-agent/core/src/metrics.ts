/**
 * Operator metrics.
 *
 * Two surfaces:
 *   - In-memory counters/gauges that the runtime increments at well-defined
 *     points. Cheap; no I/O on the hot path.
 *   - A `MetricsSnapshot` that aggregates persisted state at request time:
 *     the job journal, the settlement journal, the offline queue depth, the
 *     payout audit log. Snapshots are JSON-serializable and BigInt-safe.
 *
 * The two are joined by `MetricsRegistry.snapshot()` which returns a single
 * `Metrics` object operators can read or pipe to a dashboard.
 */

import { existsSync, readFileSync } from "node:fs";
import { join } from "node:path";

import type { Receipt } from "./billing.js";
import { JobStateStore, type JobStateRecord, type JobStatus } from "./job-state.js";
import { safeParse } from "./safe-json.js";
import { SettlementJournal, type SettlementAttempt, type SettlementStatus } from "./settlement-engine.js";
import { formatANM } from "./wallet.js";

export interface Counters {
  jobs_discovered: number;
  jobs_accepted: number;
  jobs_started: number;
  jobs_running: number;
  jobs_completed: number;
  jobs_failed_transient: number;
  jobs_failed_permanent: number;
  receipts_created: number;
  settlement_attempts: number;
  settlement_confirms: number;
  settlement_rejects: number;
  settlement_failed_transient: number;
  settlement_failed_permanent: number;
  payouts_pending: number;
  payouts_broadcast: number;
  payouts_confirmed: number;
  payouts_rejected: number;
  reserve_check_failures: number;
  artifact_bytes_total: number;
  hybrid_decisions: number;
  queued_submissions_enqueued: number;
  queued_submissions_replayed: number;
}

export const ZERO_COUNTERS = (): Counters => ({
  jobs_discovered: 0,
  jobs_accepted: 0,
  jobs_started: 0,
  jobs_running: 0,
  jobs_completed: 0,
  jobs_failed_transient: 0,
  jobs_failed_permanent: 0,
  receipts_created: 0,
  settlement_attempts: 0,
  settlement_confirms: 0,
  settlement_rejects: 0,
  settlement_failed_transient: 0,
  settlement_failed_permanent: 0,
  payouts_pending: 0,
  payouts_broadcast: 0,
  payouts_confirmed: 0,
  payouts_rejected: 0,
  reserve_check_failures: 0,
  artifact_bytes_total: 0,
  hybrid_decisions: 0,
  queued_submissions_enqueued: 0,
  queued_submissions_replayed: 0,
});

export interface RevenueByAddress {
  address: string;
  rawTotal: bigint;
  formattedANM: string;
}

export interface MetricsSnapshot {
  counters: Counters;
  jobs: {
    total: number;
    inFlight: number;
    terminal: number;
    byStatus: Record<JobStatus, number>;
    lastUpdatedAt?: string;
  };
  settlements: {
    total: number;
    byStatus: Record<SettlementStatus, number>;
    pendingByAge: { ageBucket: string; count: number }[];
    paidTotalRaw: bigint;
    paidTotalFormatted: string;
  };
  revenue: {
    byWorker: { worker: string; rawTotal: bigint; formattedANM: string }[];
    byAddress: RevenueByAddress[];
  };
  queue: {
    depth: number;
  };
  generatedAt: string;
}

const ZERO_JOB_STATUS = (): Record<JobStatus, number> => ({
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

const ZERO_SETTLEMENT_STATUS = (): Record<SettlementStatus, number> => ({
  pending_submission: 0,
  submitted: 0,
  confirming: 0,
  confirmed: 0,
  paid: 0,
  rejected: 0,
  failed_transient: 0,
  failed_permanent: 0,
  expired: 0,
});

export class MetricsRegistry {
  private counters: Counters = ZERO_COUNTERS();

  inc<K extends keyof Counters>(key: K, by = 1): void {
    this.counters[key] = (this.counters[key] ?? 0) + by;
  }
  set<K extends keyof Counters>(key: K, value: number): void {
    this.counters[key] = value;
  }
  snapshotCounters(): Counters {
    return { ...this.counters };
  }

  snapshot(stateDir: string, opts: { queueDepth?: number; receipts?: Receipt[]; jobs?: JobStateRecord[]; settlements?: SettlementAttempt[] } = {}): MetricsSnapshot {
    const jobs = opts.jobs ?? loadJobs(stateDir);
    const settlements = opts.settlements ?? loadSettlements(stateDir);
    const receipts = opts.receipts ?? loadReceipts(stateDir);
    const jobsAgg = aggregateJobs(jobs);
    const settAgg = aggregateSettlements(settlements);
    const revenue = aggregateRevenue(jobs, receipts);
    return {
      counters: this.snapshotCounters(),
      jobs: jobsAgg,
      settlements: settAgg,
      revenue,
      queue: { depth: opts.queueDepth ?? 0 },
      generatedAt: new Date().toISOString(),
    };
  }
}

function loadJobs(stateDir: string): JobStateRecord[] {
  const store = new JobStateStore(stateDir);
  store.reload();
  return store.list();
}

function loadSettlements(stateDir: string): SettlementAttempt[] {
  const journal = new SettlementJournal(stateDir);
  journal.reload();
  return journal.list();
}

function loadReceipts(stateDir: string): Receipt[] {
  const file = join(stateDir, "receipts.jsonl");
  if (!existsSync(file)) return [];
  const out: Receipt[] = [];
  for (const line of readFileSync(file, "utf8").split(/\r?\n/)) {
    if (!line) continue;
    try {
      out.push(safeParse<Receipt>(line));
    } catch {
      /* skip */
    }
  }
  return out;
}

function aggregateJobs(jobs: JobStateRecord[]): MetricsSnapshot["jobs"] {
  const byStatus = ZERO_JOB_STATUS();
  let inFlight = 0;
  let terminal = 0;
  let lastUpdatedAt: string | undefined;
  for (const j of jobs) {
    byStatus[j.status] = (byStatus[j.status] ?? 0) + 1;
    if (j.status === "paid" || j.status === "rejected_remote" || j.status === "failed") terminal++;
    else inFlight++;
    if (!lastUpdatedAt || j.updatedAt > lastUpdatedAt) lastUpdatedAt = j.updatedAt;
  }
  return { total: jobs.length, inFlight, terminal, byStatus, lastUpdatedAt };
}

function aggregateSettlements(attempts: SettlementAttempt[]): MetricsSnapshot["settlements"] {
  const byStatus = ZERO_SETTLEMENT_STATUS();
  const pendingBuckets = new Map<string, number>();
  let paidRaw = 0n;
  const now = Date.now();
  for (const a of attempts) {
    byStatus[a.status] = (byStatus[a.status] ?? 0) + 1;
    if (a.status === "paid") paidRaw += a.amountRaw;
    if (a.status === "pending_submission" || a.status === "submitted" || a.status === "confirming") {
      const ageMs = now - new Date(a.updatedAt).getTime();
      const bucket = ageBucket(ageMs);
      pendingBuckets.set(bucket, (pendingBuckets.get(bucket) ?? 0) + 1);
    }
  }
  return {
    total: attempts.length,
    byStatus,
    pendingByAge: [...pendingBuckets.entries()]
      .map(([ageBucket, count]) => ({ ageBucket, count }))
      .sort((a, b) => a.ageBucket.localeCompare(b.ageBucket)),
    paidTotalRaw: paidRaw,
    paidTotalFormatted: formatANM(paidRaw),
  };
}

function ageBucket(ms: number): string {
  if (ms < 60_000) return "<1m";
  if (ms < 5 * 60_000) return "<5m";
  if (ms < 30 * 60_000) return "<30m";
  if (ms < 60 * 60_000) return "<1h";
  if (ms < 24 * 60 * 60_000) return "<24h";
  return ">=24h";
}

function aggregateRevenue(jobs: JobStateRecord[], receipts: Receipt[]): MetricsSnapshot["revenue"] {
  const byWorker = new Map<string, bigint>();
  const byAddress = new Map<string, bigint>();
  for (const r of receipts) {
    if (r.status !== "settled") continue;
    const raw = r.actualCostRaw ?? r.estimate.raw;
    const worker = r.worker ?? "(unknown)";
    const address = r.wallet ?? "(unknown)";
    byWorker.set(worker, (byWorker.get(worker) ?? 0n) + raw);
    byAddress.set(address, (byAddress.get(address) ?? 0n) + raw);
  }
  void jobs;
  return {
    byWorker: [...byWorker.entries()]
      .map(([worker, rawTotal]) => ({ worker, rawTotal, formattedANM: formatANM(rawTotal) }))
      .sort((a, b) => (a.rawTotal > b.rawTotal ? -1 : a.rawTotal < b.rawTotal ? 1 : 0)),
    byAddress: [...byAddress.entries()]
      .map(([address, rawTotal]) => ({ address, rawTotal, formattedANM: formatANM(rawTotal) }))
      .sort((a, b) => (a.rawTotal > b.rawTotal ? -1 : a.rawTotal < b.rawTotal ? 1 : 0)),
  };
}
