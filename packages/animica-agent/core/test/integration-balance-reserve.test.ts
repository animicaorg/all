import { mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { describe, expect, it } from "vitest";

import {
  BalanceAwarePayoutGuard,
  type BalanceLookup,
  type BalanceProvider,
} from "../src/index.js";
import { BillingEngine, OfflineSettlement } from "../src/billing.js";
import { DEFAULT_CONFIG } from "../src/config.js";
import { MetricsRegistry } from "../src/metrics.js";
import { MinerRuntime } from "../src/miner-runtime.js";
import { PayoutAuditor } from "../src/payout-policy.js";
import { LocalCoordinator, type Job } from "../src/useful-work.js";

const MINER = "anm1qpzry9x8gf2tvdw0s3jn54khce6mua7l";

function makeJob(): Job {
  return {
    id: "rsv-job-1",
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

function makeProvider(lookup: BalanceLookup): BalanceProvider {
  return {
    async lookup() {
      return lookup;
    },
    invalidate() {},
  };
}

describe("Runtime reserve-balance enforcement (live-fetched balance)", () => {
  it("refuses payout when signer balance would drop below reserve", async () => {
    const dir = mkdtempSync(join(tmpdir(), "rsv-"));
    const cfg = {
      ...DEFAULT_CONFIG,
      workspacePath: dir,
      minerAddress: MINER,
      workerName: "rsv-worker",
      chainId: "1",
    };
    const coord = new LocalCoordinator({ dataDir: join(dir, "coord"), fixtureJobs: [makeJob()] });
    const auditor = new PayoutAuditor(dir);
    // Balance of 100; receipt cost ~0.001 ANM = 1_000_000_000_000_000 raw; reserve set absurdly high
    const provider = makeProvider({
      ok: true,
      balance: { address: MINER, raw: 100n, decimal: "100", formattedANM: "0", reachable: true },
      observedChainId: "1",
      fetchedAt: new Date().toISOString(),
      cached: false,
    });
    const guard = new BalanceAwarePayoutGuard({
      signerAddress: MINER,
      balanceProvider: provider,
      cfg: { bypass: false, mandatoryArtifactHash: false, minMaturityMs: 0, reserveBalanceRaw: 99n },
      auditor,
    });
    const billing = new BillingEngine(dir, cfg, undefined, new OfflineSettlement());
    const runtime = new MinerRuntime(cfg, {
      coordinator: coord,
      stateDir: dir,
      billing,
      payoutGuard: guard,
      metrics: new MetricsRegistry(),
      concurrency: 1,
      idleSleepMs: 1,
    });
    const r = await runtime.runOnce(5);
    expect(r.records[0].status).toBe("failed");
    expect(r.records[0].failureClass).toBe("permanent");
    expect(r.records[0].reason).toMatch(/payout policy/);
    const decisions = auditor.recent();
    expect(decisions[0].allowed).toBe(false);
    expect(decisions[0].reason).toBe("reserve-balance-violation");
    rmSync(dir, { recursive: true });
  });

  it("refuses payout when the balance lookup fails (rpc-unavailable)", async () => {
    const dir = mkdtempSync(join(tmpdir(), "rsv-"));
    const cfg = {
      ...DEFAULT_CONFIG,
      workspacePath: dir,
      minerAddress: MINER,
      workerName: "rsv-worker",
      chainId: "1",
    };
    const coord = new LocalCoordinator({ dataDir: join(dir, "coord"), fixtureJobs: [makeJob()] });
    const auditor = new PayoutAuditor(dir);
    const provider = makeProvider({
      ok: false,
      failureReason: "rpc-unavailable",
      message: "ECONNREFUSED",
      fetchedAt: new Date().toISOString(),
    });
    const guard = new BalanceAwarePayoutGuard({
      signerAddress: MINER,
      balanceProvider: provider,
      cfg: { mandatoryArtifactHash: false, minMaturityMs: 0 },
      auditor,
    });
    const billing = new BillingEngine(dir, cfg, undefined, new OfflineSettlement());
    const runtime = new MinerRuntime(cfg, {
      coordinator: coord,
      stateDir: dir,
      billing,
      payoutGuard: guard,
      metrics: new MetricsRegistry(),
      concurrency: 1,
      idleSleepMs: 1,
    });
    const r = await runtime.runOnce(5);
    expect(r.records[0].status).toBe("failed");
    expect(r.records[0].failureClass).toBe("permanent");
    const decisions = auditor.recent();
    expect(decisions[0].allowed).toBe(false);
    expect(decisions[0].reason).toBe("reserve-balance-violation");
    rmSync(dir, { recursive: true });
  });

  it("allows payout when signer has comfortable balance", async () => {
    const dir = mkdtempSync(join(tmpdir(), "rsv-"));
    const cfg = {
      ...DEFAULT_CONFIG,
      workspacePath: dir,
      minerAddress: MINER,
      workerName: "rsv-worker",
      chainId: "1",
    };
    const coord = new LocalCoordinator({ dataDir: join(dir, "coord"), fixtureJobs: [makeJob()] });
    const auditor = new PayoutAuditor(dir);
    const provider = makeProvider({
      ok: true,
      balance: {
        address: MINER,
        raw: 100_000_000_000_000_000_000n, // 100 ANM
        decimal: "100000000000000000000",
        formattedANM: "100",
        reachable: true,
      },
      observedChainId: "1",
      fetchedAt: new Date().toISOString(),
      cached: false,
    });
    const guard = new BalanceAwarePayoutGuard({
      signerAddress: MINER,
      balanceProvider: provider,
      cfg: { mandatoryArtifactHash: false, minMaturityMs: 0, reserveBalanceRaw: 10_000_000_000_000_000n },
      auditor,
    });
    const billing = new BillingEngine(dir, cfg, undefined, new OfflineSettlement());
    const runtime = new MinerRuntime(cfg, {
      coordinator: coord,
      stateDir: dir,
      billing,
      payoutGuard: guard,
      metrics: new MetricsRegistry(),
      concurrency: 1,
      idleSleepMs: 1,
    });
    const r = await runtime.runOnce(5);
    expect(r.records[0].status).toBe("paid");
    const decisions = auditor.recent();
    expect(decisions[0].allowed).toBe(true);
    rmSync(dir, { recursive: true });
  });
});
