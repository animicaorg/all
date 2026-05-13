import { mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { describe, expect, it } from "vitest";

import { BillingEngine, OfflineSettlement } from "../src/billing.js";
import { DEFAULT_CONFIG } from "../src/config.js";
import { MetricsRegistry } from "../src/metrics.js";
import { MinerRuntime } from "../src/miner-runtime.js";
import { PayoutAuditor, PolicyPayoutGuard } from "../src/payout-policy.js";
import { LocalCoordinator, type Job } from "../src/useful-work.js";

const MINER = "anm1qpzry9x8gf2tvdw0s3jn54khce6mua7l";

function makeJob(): Job {
  return {
    id: "int-job-1",
    kind: "eval-bench",
    tier: "cpu-light",
    modelTarget: "animica-agent",
    modelVersion: "v0",
    dataManifest:
      'data:{"prompt":"q","expected":"a b c","predicted":"a b c"}',
    hyperparams: {},
    rewardCapRaw: 1_000_000_000_000_000_000n,
    publishedAt: new Date().toISOString(),
    expiresAt: new Date(Date.now() + 86_400_000).toISOString(),
    rules: "jaccard",
  };
}

describe("Hardened pipeline: discover→run→artifact→receipt→policy→accounted", () => {
  it("a permissive policy lets the receipt reach paid and updates metrics", async () => {
    const dir = mkdtempSync(join(tmpdir(), "int-hard-"));
    const cfg = {
      ...DEFAULT_CONFIG,
      workspacePath: dir,
      minerAddress: MINER,
      workerName: "int-worker",
      chainId: "1",
    };
    const coord = new LocalCoordinator({ dataDir: join(dir, "coord"), fixtureJobs: [makeJob()] });
    const auditor = new PayoutAuditor(dir);
    const guard = new PolicyPayoutGuard(
      // Permit everything: receipts in tests are seconds-old, so bypass maturity.
      { bypass: true },
      auditor,
    );
    const metrics = new MetricsRegistry();
    const billing = new BillingEngine(dir, cfg, undefined, new OfflineSettlement());
    const runtime = new MinerRuntime(cfg, {
      coordinator: coord,
      stateDir: dir,
      billing,
      payoutGuard: guard,
      metrics,
      concurrency: 1,
      idleSleepMs: 1,
    });
    const r = await runtime.runOnce(5);
    expect(r.records[0].status).toBe("paid");
    const counters = metrics.snapshotCounters();
    expect(counters.jobs_discovered).toBe(1);
    expect(counters.jobs_accepted).toBe(1);
    expect(counters.jobs_started).toBe(1);
    expect(counters.settlement_attempts).toBeGreaterThanOrEqual(1);
    expect(counters.settlement_confirms).toBeGreaterThanOrEqual(1);
    // Auditor must have recorded the decision.
    const decisions = auditor.recent();
    expect(decisions.length).toBe(1);
    expect(decisions[0].allowed).toBe(true);
    rmSync(dir, { recursive: true });
  });

  it("a strict policy refuses below-maturity receipts and the job is failed_permanent", async () => {
    const dir = mkdtempSync(join(tmpdir(), "int-hard-"));
    const cfg = {
      ...DEFAULT_CONFIG,
      workspacePath: dir,
      minerAddress: MINER,
      workerName: "int-worker",
      chainId: "1",
    };
    const coord = new LocalCoordinator({ dataDir: join(dir, "coord"), fixtureJobs: [makeJob()] });
    const auditor = new PayoutAuditor(dir);
    // Default policy enforces 60s maturity; a freshly-issued receipt fails this.
    const guard = new PolicyPayoutGuard({}, auditor);
    const metrics = new MetricsRegistry();
    const runtime = new MinerRuntime(cfg, {
      coordinator: coord,
      stateDir: dir,
      payoutGuard: guard,
      metrics,
      concurrency: 1,
      idleSleepMs: 1,
    });
    const r = await runtime.runOnce(5);
    expect(r.records[0].status).toBe("failed");
    expect(r.records[0].failureClass).toBe("permanent");
    expect(r.records[0].reason).toMatch(/payout policy/);
    const decisions = auditor.recent();
    expect(decisions[0].allowed).toBe(false);
    expect(decisions[0].reason).toBe("below-maturity");
    rmSync(dir, { recursive: true });
  });
});
