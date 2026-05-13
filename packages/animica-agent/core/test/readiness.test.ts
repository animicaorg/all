import { mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { describe, expect, it } from "vitest";

import type { BalanceLookup, BalanceProvider } from "../src/balance-provider.js";
import { DEFAULT_CONFIG } from "../src/config.js";
import {
  READINESS_FAILURE_GUIDE,
  usefulWorkReadiness,
} from "../src/readiness.js";

const SIGNER = "anm1qpzry9x8gf2tvdw0s3jn54khce6mua7l";

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
    balance: { address: SIGNER, raw, decimal: raw.toString(), formattedANM: "0", reachable: true },
    observedChainId: "1",
    fetchedAt: new Date().toISOString(),
    cached: false,
  };
}

describe("usefulWorkReadiness", () => {
  it("returns ok=true with sufficient balance and no blockers (network skipped)", async () => {
    const dir = mkdtempSync(join(tmpdir(), "rd-"));
    const r = await usefulWorkReadiness(
      { ...DEFAULT_CONFIG, minerAddress: SIGNER, workspacePath: dir, chainId: "1" },
      {
        stateDir: dir,
        network: false,
        balanceProvider: provider(okBalance(1_000_000_000_000_000_000n)), // 1 ANM
      },
    );
    expect(r.ok).toBe(true);
    expect(r.blockers.length).toBe(0);
    expect(r.summary).toMatch(/GO/);
    rmSync(dir, { recursive: true });
  });

  it("fails when no signer address is configured", async () => {
    const dir = mkdtempSync(join(tmpdir(), "rd-"));
    const r = await usefulWorkReadiness(
      { ...DEFAULT_CONFIG, minerAddress: undefined, workspacePath: dir },
      { stateDir: dir, network: false },
    );
    expect(r.ok).toBe(false);
    expect(r.blockers.some((b) => b.name === "wallet.identity")).toBe(true);
    rmSync(dir, { recursive: true });
  });

  it("fails when balance lookup fails", async () => {
    const dir = mkdtempSync(join(tmpdir(), "rd-"));
    const r = await usefulWorkReadiness(
      { ...DEFAULT_CONFIG, minerAddress: SIGNER, workspacePath: dir, chainId: "1" },
      {
        stateDir: dir,
        network: false,
        balanceProvider: provider({
          ok: false,
          failureReason: "rpc-unavailable",
          message: "ECONNREFUSED",
          fetchedAt: new Date().toISOString(),
        }),
      },
    );
    expect(r.ok).toBe(false);
    expect(r.blockers.some((b) => b.name === "balance.lookup")).toBe(true);
    rmSync(dir, { recursive: true });
  });

  it("fails when balance is below reserve", async () => {
    const dir = mkdtempSync(join(tmpdir(), "rd-"));
    const r = await usefulWorkReadiness(
      { ...DEFAULT_CONFIG, minerAddress: SIGNER, workspacePath: dir, chainId: "1" },
      {
        stateDir: dir,
        network: false,
        balanceProvider: provider(okBalance(1n)),
        policy: { reserveBalanceRaw: 1_000_000_000_000_000_000n, mandatoryArtifactHash: false },
      },
    );
    expect(r.ok).toBe(false);
    expect(r.blockers.some((b) => b.name === "balance.reserve")).toBe(true);
    rmSync(dir, { recursive: true });
  });

  it("ok with policy reserve=0 even with low balance", async () => {
    const dir = mkdtempSync(join(tmpdir(), "rd-"));
    const r = await usefulWorkReadiness(
      { ...DEFAULT_CONFIG, minerAddress: SIGNER, workspacePath: dir, chainId: "1" },
      {
        stateDir: dir,
        network: false,
        balanceProvider: provider(okBalance(1n)),
        policy: { reserveBalanceRaw: 0n },
      },
    );
    expect(r.ok).toBe(true);
    rmSync(dir, { recursive: true });
  });

  it("READINESS_FAILURE_GUIDE has an entry for every emitted error-level check name", () => {
    const required = [
      "wallet.identity",
      "rpc.reachable",
      "rpc.chain-id",
      "balance.lookup",
      "balance.reserve",
      "coordinator.health",
      "coordinator.auth",
      "journal.health",
      "journal.size",
      "settlement.queue-stale",
      "hybrid.plan",
      "state.dir",
      "miner.eligibility",
    ];
    for (const name of required) {
      expect(READINESS_FAILURE_GUIDE[name]).toBeTruthy();
      expect(READINESS_FAILURE_GUIDE[name].fix.length).toBeGreaterThan(0);
    }
  });
});
