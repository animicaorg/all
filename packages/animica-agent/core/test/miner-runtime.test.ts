import { mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { describe, expect, it } from "vitest";

import { DEFAULT_CONFIG } from "../src/config.js";
import { JobRunner } from "../src/job-runner.js";
import { JobStateStore } from "../src/job-state.js";
import { MinerRuntime } from "../src/miner-runtime.js";
import { aggregateJobs, settlementReady, buildRollup } from "../src/rewards.js";
import { LocalCoordinator, type Job, type Submission, type VerificationOutcome } from "../src/useful-work.js";
import { BillingEngine, OfflineSettlement } from "../src/billing.js";

const MINER_ADDR = "anm1qpzry9x8gf2tvdw0s3jn54khce6mua7l";

function mkFixtureJob(overrides: Partial<Job> = {}): Job {
  return {
    id: `job-${Math.random().toString(36).slice(2, 8)}`,
    kind: "eval-bench",
    tier: "cpu-light",
    modelTarget: "animica-agent",
    modelVersion: "v0",
    dataManifest:
      'data:{"prompt":"q","expected":"the answer is forty two","predicted":"the answer is forty two"}',
    hyperparams: {},
    rewardCapRaw: 1_000_000_000_000_000_000n,
    publishedAt: new Date().toISOString(),
    expiresAt: new Date(Date.now() + 86_400_000).toISOString(),
    rules: "jaccard",
    ...overrides,
  };
}

function mkCfg(stateDir: string) {
  return {
    ...DEFAULT_CONFIG,
    workspacePath: stateDir,
    minerAddress: MINER_ADDR,
    workerName: "test-worker",
  };
}

describe("MinerRuntime integration", () => {
  it("runs the full lifecycle end-to-end (discover→paid)", async () => {
    const dir = mkdtempSync(join(tmpdir(), "miner-rt-"));
    const cfg = mkCfg(dir);
    const job = mkFixtureJob();
    const coordinator = new LocalCoordinator({ dataDir: join(dir, "coord"), fixtureJobs: [job] });
    const runtime = new MinerRuntime(cfg, {
      coordinator,
      stateDir: dir,
      concurrency: 1,
      idleSleepMs: 1,
    });

    const result = await runtime.runOnce(5);
    expect(result.records.length).toBe(1);
    const rec = result.records[0];
    expect(rec.status).toBe("paid"); // OfflineSettlement settles immediately
    expect(rec.artifactPath).toBeDefined();
    expect(rec.artifactHash).toMatch(/^[0-9a-f]{64}$/);
    expect(rec.receiptId).toBeDefined();
    expect(rec.minerAddress).toBe(MINER_ADDR);
    expect(rec.workerName).toBe("test-worker");

    // The aggregator should see exactly one paid job.
    const agg = aggregateJobs(runtime.store().list());
    expect(agg.byStatus.paid).toBe(1);
    expect(agg.terminal).toBe(1);
    expect(agg.inFlight).toBe(0);

    rmSync(dir, { recursive: true });
  });

  it("refuses unsupported kinds as permanent failure (no fabricated metric)", async () => {
    const dir = mkdtempSync(join(tmpdir(), "miner-rt-"));
    const cfg = mkCfg(dir);
    const job = mkFixtureJob({ kind: "lora-finetune" });
    const coordinator = new LocalCoordinator({ dataDir: join(dir, "coord"), fixtureJobs: [job] });
    const runtime = new MinerRuntime(cfg, { coordinator, stateDir: dir, concurrency: 1, idleSleepMs: 1 });

    const result = await runtime.runOnce(5);
    expect(result.records[0].status).toBe("failed");
    expect(result.records[0].failureClass).toBe("permanent");
    expect(result.records[0].reason).toMatch(/unsupported/i);
    rmSync(dir, { recursive: true });
  });

  it("recovers in-flight running/accepted jobs across a restart", async () => {
    const dir = mkdtempSync(join(tmpdir(), "miner-rt-"));
    const cfg = mkCfg(dir);
    // Pre-seed a state journal with a job mid-flight.
    const store = new JobStateStore(dir);
    store.discover({ jobId: "stuck", idempotencyKey: "k", workerName: "test-worker", minerAddress: MINER_ADDR });
    store.transition("stuck", "accepted");
    store.transition("stuck", "running");
    // The coordinator has the same job available.
    const coordinator = new LocalCoordinator({ dataDir: join(dir, "coord"), fixtureJobs: [mkFixtureJob({ id: "stuck" })] });
    const runtime = new MinerRuntime(cfg, { coordinator, stateDir: dir, concurrency: 1, idleSleepMs: 1 });
    // After construction, the runtime should have reset 'stuck' to discovered.
    expect(runtime.store().get("stuck")?.status).toBe("discovered");
    const result = await runtime.runOnce(5);
    const rec = result.records.find((r) => r.jobId === "stuck");
    expect(rec?.status).toBe("paid");
    rmSync(dir, { recursive: true });
  });

  it("classifies coordinator submit failures and retries transient errors", async () => {
    const dir = mkdtempSync(join(tmpdir(), "miner-rt-"));
    const cfg = mkCfg(dir);
    const job = mkFixtureJob();
    let calls = 0;
    const coordinator: Awaited<ReturnType<LocalCoordinator["listJobs"]>> extends infer T ? T : never = [job];
    // Build a hand-rolled coordinator with a transient first submit failure.
    const customCoordinator = {
      name: "test",
      async listJobs() { return [job]; },
      async getJob(id: string) { return id === job.id ? job : null; },
      async submit(sub: Submission): Promise<VerificationOutcome> {
        calls++;
        if (calls === 1) throw new Error("ETIMEDOUT during submit");
        return { submissionId: sub.id, status: "accepted", quality: 1.05, reason: "ok", verifiers: ["test"], decidedAt: new Date().toISOString() };
      },
      async recentRewards() { return []; },
      async leaderboard() { return []; },
      async adapters() { return []; },
    };
    // Use a fast sleep shim so the in-place retry backoff doesn't wall-clock.
    const runtime = new MinerRuntime(cfg, {
      coordinator: customCoordinator,
      stateDir: dir,
      concurrency: 1,
      idleSleepMs: 1,
      maxRetries: 3,
      sleep: async () => {},
    });
    const first = await runtime.runOnce(5);
    // In-place retry: first submission fails transient, second succeeds → paid.
    expect(first.records[0].status).toBe("paid");
    expect(calls).toBe(2);
    rmSync(dir, { recursive: true });
  });

  it("idempotently de-duplicates the same job across iterations", async () => {
    const dir = mkdtempSync(join(tmpdir(), "miner-rt-"));
    const cfg = mkCfg(dir);
    const job = mkFixtureJob();
    const coordinator = new LocalCoordinator({ dataDir: join(dir, "coord"), fixtureJobs: [job] });
    const runtime = new MinerRuntime(cfg, { coordinator, stateDir: dir, concurrency: 1, idleSleepMs: 1 });
    const a = await runtime.runOnce(5);
    const b = await runtime.runOnce(5);
    expect(a.records.length).toBe(1);
    expect(b.records.length).toBe(0); // already terminal
    expect(runtime.store().list()).toHaveLength(1);
    rmSync(dir, { recursive: true });
  });

  it("honors a concurrency cap", async () => {
    const dir = mkdtempSync(join(tmpdir(), "miner-rt-"));
    const cfg = mkCfg(dir);
    const jobs = Array.from({ length: 4 }, (_, i) => mkFixtureJob({ id: `j${i}` }));
    const coordinator = new LocalCoordinator({ dataDir: join(dir, "coord"), fixtureJobs: jobs });
    const runtime = new MinerRuntime(cfg, { coordinator, stateDir: dir, concurrency: 2, idleSleepMs: 1 });
    expect(runtime.concurrency()).toBe(2);
    const r = await runtime.runOnce(10);
    expect(r.records.filter((x) => x.status === "paid").length).toBe(4);
    rmSync(dir, { recursive: true });
  });

  it("buildRollup surfaces address/worker/settlement aggregates", async () => {
    const dir = mkdtempSync(join(tmpdir(), "miner-rt-"));
    const cfg = mkCfg(dir);
    const job = mkFixtureJob();
    const coordinator = new LocalCoordinator({ dataDir: join(dir, "coord"), fixtureJobs: [job] });
    const runtime = new MinerRuntime(cfg, { coordinator, stateDir: dir, concurrency: 1, idleSleepMs: 1 });
    await runtime.runOnce(5);
    const roll = buildRollup(dir, cfg);
    expect(roll.byAddress[0]?.address).toBe(MINER_ADDR);
    expect(roll.byAddress[0]?.jobsTerminal).toBe(1);
    expect(roll.byAddress[0]?.jobsPaid).toBe(1);
    expect(roll.byWorker[0]?.worker).toBe("test-worker");
    // settlementReady should be empty because the receipt was already settled offline.
    expect(roll.settlement.ready.length).toBe(0);
    rmSync(dir, { recursive: true });
  });

  it("settlementReady returns ready receipts when settlement is not auto", async () => {
    const dir = mkdtempSync(join(tmpdir(), "miner-rt-"));
    const cfg = mkCfg(dir);
    const job = mkFixtureJob();
    const coordinator = new LocalCoordinator({ dataDir: join(dir, "coord"), fixtureJobs: [job] });
    // Use a settlement backend that intentionally fails so the receipt remains unsettled.
    const failingSettlement = {
      name: "test-failing",
      async settle() {
        return { status: "failed" as const, reason: "test-only failure" };
      },
    };
    const billing = new BillingEngine(dir, cfg, undefined, failingSettlement);
    const runtime = new MinerRuntime(cfg, { coordinator, stateDir: dir, billing, concurrency: 1, idleSleepMs: 1 });
    await runtime.runOnce(5);
    const view = settlementReady(runtime.store().list(), billing.listReceipts(100));
    // Job got to settlement_pending; receipt did not settle.
    expect(view.pendingReceipts.length).toBeGreaterThan(0);
    rmSync(dir, { recursive: true });
  });
});
