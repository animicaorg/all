import { mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { describe, expect, it } from "vitest";

import type { BalanceLookup, BalanceProvider } from "../src/balance-provider.js";
import { BillingEngine, OfflineSettlement } from "../src/billing.js";
import { DEFAULT_CONFIG } from "../src/config.js";
import { MetricsRegistry } from "../src/metrics.js";
import { MinerRuntime } from "../src/miner-runtime.js";
import { PayoutAuditor } from "../src/payout-policy.js";
import { LocalCoordinator, type Job } from "../src/useful-work.js";

const MINER = "anm1qpzry9x8gf2tvdw0s3jn54khce6mua7l";

function makeJob(): Job {
  return {
    id: "ar-job-1",
    kind: "eval-bench",
    tier: "cpu-light",
    modelTarget: "animica-agent",
    modelVersion: "v0",
    dataManifest: 'data:{"prompt":"q","expected":"a","predicted":"a"}',
    hyperparams: {},
    rewardCapRaw: 1_000_000_000_000_000_000n,
    publishedAt: new Date().toISOString(),
    expiresAt: new Date(Date.now() + 86_400_000).toISOString(),
    rules: "jaccard",
  };
}

function provider(lookup: BalanceLookup): BalanceProvider {
  return {
    async lookup() {
      return lookup;
    },
    invalidate() {},
  };
}

function okBalance(raw: bigint): BalanceLookup {
  return {
    ok: true,
    balance: { address: MINER, raw, decimal: raw.toString(), formattedANM: "0", reachable: true },
    observedChainId: "1",
    fetchedAt: new Date().toISOString(),
    cached: false,
  };
}

describe("Default-on balance-aware reserve enforcement", () => {
  it("settlementMode=live auto-wires BalanceAwarePayoutGuard and refuses payout below reserve", async () => {
    const dir = mkdtempSync(join(tmpdir(), "ar-"));
    const cfg = {
      ...DEFAULT_CONFIG,
      workspacePath: dir,
      minerAddress: MINER,
      workerName: "ar-w",
      chainId: "1",
      settlementMode: "live" as const,
      reservePolicy: "strict" as const,
    };
    const coord = new LocalCoordinator({ dataDir: join(dir, "coord"), fixtureJobs: [makeJob()] });
    const billing = new BillingEngine(dir, cfg, undefined, new OfflineSettlement());
    const runtime = new MinerRuntime(cfg, {
      coordinator: coord,
      stateDir: dir,
      billing,
      metrics: new MetricsRegistry(),
      balanceProvider: provider(okBalance(1n)), // tiny balance
      payoutPolicy: { mandatoryArtifactHash: false, minMaturityMs: 0, reserveBalanceRaw: 1_000_000n },
      concurrency: 1,
      idleSleepMs: 1,
    });
    const r = await runtime.runOnce(5);
    expect(r.records[0].status).toBe("failed");
    expect(r.records[0].failureClass).toBe("permanent");
    const auditor = new PayoutAuditor(dir);
    const decisions = auditor.recent();
    expect(decisions[0].allowed).toBe(false);
    expect(decisions[0].reason).toBe("reserve-balance-violation");
    rmSync(dir, { recursive: true });
  });

  it("settlementMode=live + sufficient balance: payout allowed", async () => {
    const dir = mkdtempSync(join(tmpdir(), "ar-"));
    const cfg = {
      ...DEFAULT_CONFIG,
      workspacePath: dir,
      minerAddress: MINER,
      workerName: "ar-w",
      chainId: "1",
      settlementMode: "live" as const,
      reservePolicy: "strict" as const,
    };
    const coord = new LocalCoordinator({ dataDir: join(dir, "coord"), fixtureJobs: [makeJob()] });
    const billing = new BillingEngine(dir, cfg, undefined, new OfflineSettlement());
    const runtime = new MinerRuntime(cfg, {
      coordinator: coord,
      stateDir: dir,
      billing,
      metrics: new MetricsRegistry(),
      balanceProvider: provider(okBalance(100_000_000_000_000_000_000n)), // 100 ANM
      payoutPolicy: { mandatoryArtifactHash: false, minMaturityMs: 0, reserveBalanceRaw: 10_000_000_000_000_000n },
      concurrency: 1,
      idleSleepMs: 1,
    });
    const r = await runtime.runOnce(5);
    expect(r.records[0].status).toBe("paid");
    rmSync(dir, { recursive: true });
  });

  it("settlementMode=live + balance lookup fails: payout refused (fail closed)", async () => {
    const dir = mkdtempSync(join(tmpdir(), "ar-"));
    const cfg = {
      ...DEFAULT_CONFIG,
      workspacePath: dir,
      minerAddress: MINER,
      workerName: "ar-w",
      chainId: "1",
      settlementMode: "live" as const,
      reservePolicy: "strict" as const,
    };
    const coord = new LocalCoordinator({ dataDir: join(dir, "coord"), fixtureJobs: [makeJob()] });
    const billing = new BillingEngine(dir, cfg, undefined, new OfflineSettlement());
    const runtime = new MinerRuntime(cfg, {
      coordinator: coord,
      stateDir: dir,
      billing,
      metrics: new MetricsRegistry(),
      balanceProvider: provider({
        ok: false,
        failureReason: "rpc-unavailable",
        message: "ECONNREFUSED",
        fetchedAt: new Date().toISOString(),
      }),
      payoutPolicy: { mandatoryArtifactHash: false, minMaturityMs: 0 },
      concurrency: 1,
      idleSleepMs: 1,
    });
    const r = await runtime.runOnce(5);
    expect(r.records[0].status).toBe("failed");
    const auditor = new PayoutAuditor(dir);
    const decisions = auditor.recent();
    expect(decisions[0].allowed).toBe(false);
    expect(decisions[0].reason).toBe("reserve-balance-violation");
    rmSync(dir, { recursive: true });
  });

  it("settlementMode=offline: NO guard is auto-wired (legacy behavior preserved)", async () => {
    const dir = mkdtempSync(join(tmpdir(), "ar-"));
    const cfg = {
      ...DEFAULT_CONFIG,
      workspacePath: dir,
      minerAddress: MINER,
      workerName: "ar-w",
      chainId: "1",
      settlementMode: "offline" as const,
      reservePolicy: "off" as const,
    };
    const coord = new LocalCoordinator({ dataDir: join(dir, "coord"), fixtureJobs: [makeJob()] });
    const billing = new BillingEngine(dir, cfg, undefined, new OfflineSettlement());
    const runtime = new MinerRuntime(cfg, {
      coordinator: coord,
      stateDir: dir,
      billing,
      metrics: new MetricsRegistry(),
      concurrency: 1,
      idleSleepMs: 1,
    });
    const r = await runtime.runOnce(5);
    // Offline settlement: reaches paid without any guard consultation.
    expect(r.records[0].status).toBe("paid");
    const auditor = new PayoutAuditor(dir);
    const decisions = auditor.recent();
    expect(decisions.length).toBe(0);
    rmSync(dir, { recursive: true });
  });

  it("settlementMode=live + reservePolicy=off: NO guard is auto-wired (escape hatch)", async () => {
    const dir = mkdtempSync(join(tmpdir(), "ar-"));
    const cfg = {
      ...DEFAULT_CONFIG,
      workspacePath: dir,
      minerAddress: MINER,
      workerName: "ar-w",
      chainId: "1",
      settlementMode: "live" as const,
      reservePolicy: "off" as const,
    };
    const coord = new LocalCoordinator({ dataDir: join(dir, "coord"), fixtureJobs: [makeJob()] });
    const billing = new BillingEngine(dir, cfg, undefined, new OfflineSettlement());
    const runtime = new MinerRuntime(cfg, {
      coordinator: coord,
      stateDir: dir,
      billing,
      metrics: new MetricsRegistry(),
      concurrency: 1,
      idleSleepMs: 1,
    });
    const r = await runtime.runOnce(5);
    expect(r.records[0].status).toBe("paid");
    const auditor = new PayoutAuditor(dir);
    expect(auditor.recent().length).toBe(0);
    rmSync(dir, { recursive: true });
  });
});
