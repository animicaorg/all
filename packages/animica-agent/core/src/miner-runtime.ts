/**
 * Useful-work miner runtime.
 *
 * A real worker loop: polls the coordinator for jobs, runs them locally,
 * persists each lifecycle transition, builds a receipt, and queues the
 * submission. Designed for two modes:
 *
 *   - one-shot:  `runOnce()` processes up to `maxJobs` and returns. Used by
 *                tests and by `animica-agent miner start --once`.
 *   - daemon:    `run()` loops forever until `stop()` is called. Used by
 *                `animica-agent miner start --daemon`.
 *
 * Recovery: on construction the runtime loads the persisted journal. Any
 * non-terminal jobs are inspected; jobs in `accepted | running` are reset to
 * `discovered` (with attempt count preserved) so the next loop iteration
 * picks them up. Jobs in `submitted | accepted_remote | settlement_pending`
 * are NOT re-run — they require the settlement worker to drive them to
 * terminal state.
 *
 * Resource controls:
 *   - concurrency limit (default 1; honors resourceMode = miner-priority)
 *   - per-job timeout (default 60s, configurable)
 *   - per-iteration job cap (max-jobs)
 *   - between-iteration backoff when no work is available
 *   - graceful shutdown: a stop() call drains in-flight work, then exits
 */

import { mkdirSync } from "node:fs";
import { join } from "node:path";

import { UsageJournal } from "./accounting.js";
import { BillingEngine, OfflineSettlement, type ReceiptRequest, type SettlementBackend } from "./billing.js";
import type { AgentConfig } from "./config.js";
import { JobRunner } from "./job-runner.js";
import {
  classifyJobFailure,
  JobStateStore,
  TERMINAL_STATUSES,
  type JobStateRecord,
  type JobStatus,
} from "./job-state.js";
import { detectMinerIdentity, planResources } from "./miner.js";
import { LocalCoordinator, type Coordinator, type Job, type Submission, type VerificationOutcome } from "./useful-work.js";
import { resolveWalletIdentity } from "./wallet.js";
import {
  BalanceAwarePayoutGuard,
  DEFAULT_PAYOUT_POLICY,
  PayoutAuditor,
  type PayoutGuard,
  type PayoutPolicyConfig,
} from "./payout-policy.js";
import { RpcBalanceProvider, type BalanceProvider } from "./balance-provider.js";
import type { MetricsRegistry } from "./metrics.js";

export interface MinerRuntimeOptions {
  /** Coordinator to poll. Required. */
  coordinator: Coordinator;
  /** Persistent state dir. Conventionally <stateDir>/jobs/. */
  stateDir: string;
  /** Job runner used to execute supported job kinds. Reused by tests. */
  runner?: JobRunner;
  /** Billing engine used to create reward receipts. Defaults to offline. */
  billing?: BillingEngine;
  /** Override settlement backend used by the default billing engine. */
  settlement?: SettlementBackend;
  /** Max concurrent jobs (clamped to >=1; default min(plan.workerCount,2)). */
  concurrency?: number;
  /** Per-job timeout in ms. Default 60_000. */
  jobTimeoutMs?: number;
  /** Between-iteration sleep when there's no work. Default 5_000. */
  idleSleepMs?: number;
  /** Maximum retries per job for transient failures. Default 3. */
  maxRetries?: number;
  /** Observer hook for tests and operator UIs. */
  onTransition?: (rec: JobStateRecord) => void;
  /** Sleep shim for tests. */
  sleep?: (ms: number) => Promise<void>;
  /** Optional payout safety guard. When set, receipts are evaluated before charging. */
  payoutGuard?: PayoutGuard;
  /** Optional metrics registry. When set, counters are incremented on lifecycle events. */
  metrics?: MetricsRegistry;
  /** Optional balance provider used for the auto-wired BalanceAwarePayoutGuard
   *  when settlementMode=live and reservePolicy=strict. Tests inject fixtures. */
  balanceProvider?: BalanceProvider;
  /** Payout policy override for the auto-wired guard. Defaults to DEFAULT_PAYOUT_POLICY. */
  payoutPolicy?: PayoutPolicyConfig;
}

export interface MinerRuntimeIterationResult {
  /** Number of jobs the iteration processed to a terminal-or-submitted state. */
  processed: number;
  /** Number of jobs that ended in a non-terminal recoverable state. */
  recoverable: number;
  /** Number of jobs that failed permanently this iteration. */
  failedPermanent: number;
  /** Final state of each job seen this iteration. */
  records: JobStateRecord[];
}

export class MinerRuntime {
  private readonly cfg: AgentConfig;
  private readonly opts: Required<Omit<MinerRuntimeOptions, "onTransition" | "billing" | "settlement" | "runner" | "payoutGuard" | "metrics" | "balanceProvider" | "payoutPolicy">> &
    Pick<MinerRuntimeOptions, "onTransition" | "billing" | "settlement" | "runner" | "payoutGuard" | "metrics" | "balanceProvider" | "payoutPolicy">;
  private readonly state: JobStateStore;
  private readonly runner: JobRunner;
  private readonly billing: BillingEngine;
  private readonly usage: UsageJournal;
  private stopRequested = false;
  private inFlight = 0;

  constructor(cfg: AgentConfig, opts: MinerRuntimeOptions) {
    this.cfg = cfg;
    const stateDir = opts.stateDir;
    mkdirSync(stateDir, { recursive: true });
    const plan = planResources(cfg, true);
    this.opts = {
      coordinator: opts.coordinator,
      stateDir,
      concurrency: Math.max(1, opts.concurrency ?? Math.min(plan.workerCount, 2)),
      jobTimeoutMs: opts.jobTimeoutMs ?? 60_000,
      idleSleepMs: opts.idleSleepMs ?? 5_000,
      maxRetries: opts.maxRetries ?? 3,
      sleep: opts.sleep ?? ((ms: number) => new Promise<void>((r) => setTimeout(r, ms))),
      onTransition: opts.onTransition,
      billing: opts.billing,
      settlement: opts.settlement,
      runner: opts.runner,
      payoutGuard: opts.payoutGuard,
      metrics: opts.metrics,
      balanceProvider: opts.balanceProvider,
      payoutPolicy: opts.payoutPolicy,
    };
    this.state = new JobStateStore(stateDir);
    this.runner = opts.runner ?? new JobRunner(cfg, stateDir);
    this.billing = opts.billing ?? new BillingEngine(stateDir, cfg, undefined, opts.settlement ?? new OfflineSettlement());
    this.usage = new UsageJournal(stateDir);
    // Auto-wire BalanceAwarePayoutGuard when settlementMode=live AND
    // reservePolicy=strict (the default in live mode). Operators who pass
    // an explicit `payoutGuard` keep full control — auto-wiring only fires
    // when no guard was supplied.
    if (!this.opts.payoutGuard) {
      const effectiveReservePolicy =
        cfg.reservePolicy ?? (cfg.settlementMode === "live" ? "strict" : "off");
      if (cfg.settlementMode === "live" && effectiveReservePolicy === "strict" && cfg.minerAddress) {
        const auditor = new PayoutAuditor(stateDir);
        const provider =
          opts.balanceProvider ??
          new RpcBalanceProvider({
            rpcUrl: cfg.rpcUrl,
            expectedChainId: cfg.chainId,
          });
        this.opts.payoutGuard = new BalanceAwarePayoutGuard({
          signerAddress: cfg.minerAddress,
          balanceProvider: provider,
          cfg: opts.payoutPolicy ?? DEFAULT_PAYOUT_POLICY,
          auditor,
        });
      }
    }
    this.recoverOnStart();
  }

  /** Resets in-flight transient states so the next iteration picks them up. */
  private recoverOnStart(): void {
    for (const rec of this.state.listInFlight()) {
      if (rec.status === "accepted" || rec.status === "running") {
        // Soft-reset to discovered so the next iteration re-claims/re-runs.
        // Attempt count is preserved by the state machine (attempts only
        // increment on a transition INTO `running`).
        this.transition(rec.jobId, "discovered", {
          reason: `restart-recovery: was ${rec.status}`,
        });
      }
      // submitted | accepted_remote | settlement_pending are left for the
      // settlement worker to drive forward (idempotent on duplicate submit).
    }
  }

  /** Returns the current state journal for inspection. */
  store(): JobStateStore {
    return this.state;
  }

  /** Concurrency the runtime is operating with. */
  concurrency(): number {
    return this.opts.concurrency;
  }

  /** Request graceful stop. Returns once the in-flight set has drained. */
  async stop(): Promise<void> {
    this.stopRequested = true;
    // Cheap busy-wait; in practice the loop calls sleep() which the test
    // shim can advance directly. Bounded by maxRetries * jobTimeoutMs.
    let waited = 0;
    while (this.inFlight > 0 && waited < this.opts.jobTimeoutMs * 2) {
      await this.opts.sleep(50);
      waited += 50;
    }
  }

  /** Run a bounded iteration, processing at most `maxJobs`. */
  async runOnce(maxJobs = 5): Promise<MinerRuntimeIterationResult> {
    const result: MinerRuntimeIterationResult = {
      processed: 0,
      recoverable: 0,
      failedPermanent: 0,
      records: [],
    };
    const available = await this.discoverAndQueue(maxJobs);
    if (available.length === 0) return result;

    // Bounded concurrency. Each in-flight job tracks its own settled flag
    // synchronously so we don't depend on a buggy race-based check.
    type Tracker = { promise: Promise<void>; done: boolean };
    const inflight: Tracker[] = [];
    const sweep = () => {
      for (let i = inflight.length - 1; i >= 0; i--) {
        if (inflight[i].done) inflight.splice(i, 1);
      }
    };
    for (const jobId of available) {
      while (inflight.length >= this.opts.concurrency) {
        await Promise.race(inflight.map((t) => t.promise));
        sweep();
      }
      const tracker: Tracker = { promise: Promise.resolve(), done: false };
      tracker.promise = this.processOne(jobId)
        .then((rec) => {
          result.records.push(rec);
          if (TERMINAL_STATUSES.has(rec.status) || rec.status === "submitted" || rec.status === "accepted_remote") {
            result.processed++;
          } else {
            result.recoverable++;
          }
          if (rec.status === "failed" && rec.failureClass === "permanent") {
            result.failedPermanent++;
          }
        })
        .finally(() => {
          tracker.done = true;
        });
      inflight.push(tracker);
    }
    await Promise.all(inflight.map((t) => t.promise));
    return result;
  }

  /**
   * Daemon mode: loop indefinitely until stop() is called.
   * Returns aggregate counts across all iterations.
   */
  async run(opts: { maxIterations?: number; maxJobsPerIteration?: number } = {}): Promise<MinerRuntimeIterationResult> {
    const max = opts.maxIterations ?? Infinity;
    const perIter = opts.maxJobsPerIteration ?? 5;
    const totals: MinerRuntimeIterationResult = {
      processed: 0,
      recoverable: 0,
      failedPermanent: 0,
      records: [],
    };
    let iters = 0;
    while (!this.stopRequested && iters < max) {
      const r = await this.runOnce(perIter);
      totals.processed += r.processed;
      totals.recoverable += r.recoverable;
      totals.failedPermanent += r.failedPermanent;
      totals.records.push(...r.records);
      iters++;
      if (this.stopRequested) break;
      if (r.processed === 0 && r.recoverable === 0) {
        await this.opts.sleep(this.opts.idleSleepMs);
      }
    }
    return totals;
  }

  /** Discover up to maxJobs new jobs and seed state records. Returns jobIds to drive this iteration. */
  private async discoverAndQueue(maxJobs: number): Promise<string[]> {
    const out: string[] = [];
    // First pick up anything already in a runnable state (discovered).
    for (const rec of this.state.list()) {
      if (rec.status === "discovered") {
        out.push(rec.jobId);
        if (out.length >= maxJobs) return out;
      }
    }
    // Then poll the coordinator for new work.
    let jobs: Job[];
    try {
      jobs = await this.opts.coordinator.listJobs();
    } catch (err) {
      // Coordinator errors are transient; the next iteration retries.
      return out;
    }
    const worker = this.cfg.workerName;
    const minerAddress = detectMinerIdentity(this.cfg).payoutAddress ?? resolveWalletIdentity(this.cfg)?.address;
    for (const job of jobs) {
      if (out.length >= maxJobs) break;
      const existing = this.state.get(job.id);
      if (existing) continue; // skip already-tracked work
      const rec = this.state.discover({
        jobId: job.id,
        idempotencyKey: `${job.id}:${minerAddress ?? "anon"}`,
        workerName: worker,
        minerAddress,
        labels: {
          kind: job.kind,
          tier: job.tier,
          modelTarget: job.modelTarget,
          modelVersion: job.modelVersion,
        },
      });
      this.notify(rec);
      this.opts.metrics?.inc("jobs_discovered");
      out.push(job.id);
    }
    return out;
  }

  /** Drive a single job through one full lifecycle pass. Returns the final record for this pass. */
  private async processOne(jobId: string): Promise<JobStateRecord> {
    this.inFlight++;
    try {
      // accept
      let rec = this.transition(jobId, "accepted", { reason: "claimed" });
      this.opts.metrics?.inc("jobs_accepted");
      const job = await this.opts.coordinator.getJob(jobId);
      if (!job) {
        return this.transition(jobId, "failed", {
          reason: "coordinator no longer lists the job",
          failureClass: "permanent",
        });
      }
      // Refuse permanently if the runner does not support this job kind. The
      // runner's `kind: "unsupported"` path is honored.
      // run
      rec = this.transition(jobId, "running", { reason: "starting local execution" });
      this.opts.metrics?.inc("jobs_started");
      this.opts.metrics?.inc("jobs_running");
      let runResult: Awaited<ReturnType<JobRunner["run"]>>;
      try {
        runResult = await withTimeout(this.runner.run(job), this.opts.jobTimeoutMs);
      } catch (err) {
        const msg = err instanceof Error ? err.message : String(err);
        const cls = classifyJobFailure(msg);
        return this.transition(jobId, "failed", {
          reason: `runner threw: ${msg.slice(0, 256)}`,
          failureClass: cls,
        });
      }
      if (runResult.kind === "unsupported") {
        return this.transition(jobId, "failed", {
          reason: `unsupported job kind; refusing rather than fabricating: ${runResult.reason ?? ""}`,
          failureClass: "permanent",
        });
      }
      // completed → artifacted
      rec = this.transition(jobId, "completed", { reason: "execution succeeded" });
      rec = this.transition(jobId, "artifacted", {
        artifactPath: runResult.artifactPath,
        artifactHash: runResult.submission.artifactHash,
        reason: "artifact written",
      });
      // submit (retry in-place on transient failures; the state machine does
      // not permit artifacted→discovered, and submission is idempotent at
      // the coordinator because every Submission carries a stable id).
      let outcome: VerificationOutcome;
      let submitErr: unknown;
      const maxAttempts = this.opts.maxRetries;
      let success = false;
      for (let attempt = 1; attempt <= maxAttempts; attempt++) {
        try {
          outcome = await this.opts.coordinator.submit(runResult.submission);
          success = true;
          break;
        } catch (err) {
          submitErr = err;
          const msg = err instanceof Error ? err.message : String(err);
          const cls = classifyJobFailure(msg);
          if (cls !== "transient") break;
          // Brief backoff between attempts. Tests provide a fast sleep shim.
          if (attempt < maxAttempts) await this.opts.sleep(Math.min(1000 * attempt, 5000));
        }
      }
      if (!success) {
        const msg = submitErr instanceof Error ? submitErr.message : String(submitErr);
        const cls = classifyJobFailure(msg);
        return this.transition(jobId, "failed", {
          reason: `submit failed after retries: ${msg.slice(0, 200)}`,
          failureClass: cls,
        });
      }
      // outcome is guaranteed assigned by the success path above.
      const verifiedOutcome = outcome!;
      outcome = verifiedOutcome;
      rec = this.transition(jobId, "submitted", {
        reason: `submitted to coordinator ${this.opts.coordinator.name}`,
      });
      // Verify outcome
      if (outcome.status === "rejected") {
        return this.transition(jobId, "rejected_remote", {
          reason: outcome.reason || "remote rejected",
        });
      }
      if (outcome.status === "accepted") {
        rec = this.transition(jobId, "accepted_remote", { reason: outcome.reason || "remote accepted" });
        // Create a reward receipt. We use the artifact hash as the
        // idempotency key so retries during settlement are safe.
        const idempotencyKey = `useful-work:${jobId}:${runResult.submission.artifactHash}`;
        const est = this.billing.estimate({ kind: "scaffold", premium: false });
        const receiptReq: ReceiptRequest = {
          kind: "scaffold",
          estimate: est,
          status: "accepted_remote" === "accepted_remote" ? "estimated" : "estimated",
          wallet: rec.minerAddress,
          worker: rec.workerName,
          toolsUsed: ["useful-work-runner", `coordinator:${this.opts.coordinator.name}`],
          elapsedMs: runResult.submission.elapsedMs,
          idempotencyKey,
        };
        try {
          // Build a draft receipt so the payout guard can inspect it.
          // BillingEngine.charge with status="estimated" creates the receipt
          // first; we evaluate the guard between create and the settled state.
          const receipt = await this.billing.charge(receiptReq);
          this.opts.metrics?.inc("receipts_created");
          this.opts.metrics?.inc("payouts_pending");
          rec = this.transition(jobId, "settlement_pending", {
            receiptId: receipt.id,
            reason: `receipt ${receipt.id.slice(0, 8)} ready for settlement`,
          });
          // Payout guard: only consulted when explicitly configured. If it
          // refuses, the receipt stays at status "settled" (offline) but the
          // job is moved to failed with a policy reason rather than paid.
          if (this.opts.payoutGuard && rec.minerAddress) {
            const guard = await this.opts.payoutGuard.decide({
              receipt,
              worker: rec.workerName,
              recipient: rec.minerAddress,
              amountRaw: receipt.actualCostRaw ?? receipt.estimate.raw,
              artifactHash: runResult.submission.artifactHash,
              chainId: this.cfg.chainId,
            });
            if (!guard.allowed) {
              if (guard.evaluation.reason === "reserve-balance-violation") {
                this.opts.metrics?.inc("reserve_check_failures");
              }
              return this.transition(jobId, "failed", {
                reason: `payout policy: ${guard.evaluation.reason ?? "denied"} (${guard.evaluation.message ?? ""})`,
                failureClass: "permanent",
              });
            }
          }
          if (receipt.status === "settled") {
            rec = this.transition(jobId, "paid", {
              txHash: receipt.txHash,
              reason: receipt.txHash ? `settled tx=${receipt.txHash.slice(0, 12)}…` : "settled (offline)",
            });
          }
          this.opts.metrics?.inc("settlement_attempts");
          if (receipt.status === "settled") {
            this.opts.metrics?.inc("settlement_confirms");
            this.opts.metrics?.inc("payouts_confirmed");
          }
          this.usage.record({
            kind: "scaffold",
            detail: { useful_work: true, jobId, jobKind: job.kind },
            attribution: {
              minerAddress: rec.minerAddress,
              walletAddress: rec.minerAddress,
              worker: rec.workerName,
              creditsMode: this.cfg.creditsMode,
              aicfMode: this.cfg.aicfMode,
            },
          });
        } catch (err) {
          const msg = err instanceof Error ? err.message : String(err);
          // Receipt-creation failure is recoverable from settlement_pending.
          return this.transition(jobId, "failed", {
            reason: `receipt error: ${msg.slice(0, 200)}`,
            failureClass: classifyJobFailure(msg),
          });
        }
        return rec;
      }
      // Pending or challenged → leave at submitted; settlement worker drives forward.
      return rec;
    } finally {
      this.inFlight--;
    }
  }

  /** Internal helper that records the transition and fires the observer. */
  private transition(jobId: string, to: JobStatus, patch: Parameters<JobStateStore["transition"]>[2] = {}): JobStateRecord {
    const rec = this.state.transition(jobId, to, patch);
    this.notify(rec);
    return rec;
  }

  private notify(rec: JobStateRecord): void {
    try {
      this.opts.onTransition?.(rec);
    } catch {
      /* observers must not crash the runtime */
    }
  }
}

async function withTimeout<T>(p: Promise<T>, ms: number): Promise<T> {
  let to: ReturnType<typeof setTimeout> | undefined;
  const timeout = new Promise<never>((_, reject) => {
    to = setTimeout(() => reject(new Error(`timeout after ${ms}ms`)), ms);
  });
  try {
    return await Promise.race([p, timeout]);
  } finally {
    if (to) clearTimeout(to);
  }
}

/** Convenience constructor — sets up a LocalCoordinator backed by a fixtures file. */
export function localMinerRuntime(cfg: AgentConfig, stateDir: string, options: Partial<MinerRuntimeOptions> = {}): MinerRuntime {
  const coordDir = join(stateDir, "coordinator");
  mkdirSync(coordDir, { recursive: true });
  const coordinator = new LocalCoordinator({ dataDir: coordDir });
  return new MinerRuntime(cfg, { coordinator, stateDir, ...options });
}
