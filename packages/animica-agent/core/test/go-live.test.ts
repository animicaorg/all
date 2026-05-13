import { appendFileSync, mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { describe, expect, it } from "vitest";

import type { BalanceLookup, BalanceProvider } from "../src/balance-provider.js";
import { DEFAULT_CONFIG } from "../src/config.js";
import { goLive } from "../src/go-live.js";
import { safeStringify } from "../src/safe-json.js";

const SIGNER = "anm1qpzry9x8gf2tvdw0s3jn54khce6mua7l";

function provider(lookup: BalanceLookup): BalanceProvider {
  return {
    async lookup() {
      return lookup;
    },
    invalidate() {},
  };
}

function seedFreshReport(stateDir: string) {
  appendFileSync(
    join(stateDir, "coordinator-verifications.jsonl"),
    safeStringify({
      id: "r-1",
      baseUrl: "http://coord",
      generatedAt: new Date().toISOString(),
      ok: true,
      checks: [],
      summary: "ok",
    }) + "\n",
    "utf8",
  );
}

describe("goLive", () => {
  it("returns ok=true when every gate passes (live + strict + fresh + balance)", async () => {
    const dir = mkdtempSync(join(tmpdir(), "gl-"));
    seedFreshReport(dir);
    const cfg = {
      ...DEFAULT_CONFIG,
      workspacePath: dir,
      minerAddress: SIGNER,
      chainId: "1",
      settlementMode: "live" as const,
      reservePolicy: "strict" as const,
    };
    const r = await goLive(cfg, {
      stateDir: dir,
      network: false,
      balanceProvider: provider({
        ok: true,
        balance: { address: SIGNER, raw: 100_000_000_000_000_000_000n, decimal: "", formattedANM: "100", reachable: true },
        observedChainId: "1",
        fetchedAt: new Date().toISOString(),
        cached: false,
      }),
      policy: { reserveBalanceRaw: 10_000_000_000_000_000n },
    });
    expect(r.ok).toBe(true);
    expect(r.summary).toMatch(/GO/);
    rmSync(dir, { recursive: true });
  });

  it("fails when no coordinator verification exists", async () => {
    const dir = mkdtempSync(join(tmpdir(), "gl-"));
    const cfg = {
      ...DEFAULT_CONFIG,
      workspacePath: dir,
      minerAddress: SIGNER,
      settlementMode: "live" as const,
      reservePolicy: "strict" as const,
    };
    const r = await goLive(cfg, {
      stateDir: dir,
      network: false,
      balanceProvider: provider({
        ok: true,
        balance: { address: SIGNER, raw: 1n, decimal: "1", formattedANM: "0", reachable: true },
        observedChainId: "1",
        fetchedAt: new Date().toISOString(),
        cached: false,
      }),
      policy: { reserveBalanceRaw: 0n },
    });
    expect(r.ok).toBe(false);
    expect(r.checks.some((c) => c.name === "coordinator-fresh" && !c.ok)).toBe(true);
    rmSync(dir, { recursive: true });
  });

  it("fails when settlementMode=offline (no autonomous mainnet payout)", async () => {
    const dir = mkdtempSync(join(tmpdir(), "gl-"));
    seedFreshReport(dir);
    const cfg = {
      ...DEFAULT_CONFIG,
      workspacePath: dir,
      minerAddress: SIGNER,
      settlementMode: "offline" as const,
    };
    const r = await goLive(cfg, {
      stateDir: dir,
      network: false,
      balanceProvider: provider({
        ok: true,
        balance: { address: SIGNER, raw: 1n, decimal: "1", formattedANM: "0", reachable: true },
        observedChainId: "1",
        fetchedAt: new Date().toISOString(),
        cached: false,
      }),
    });
    expect(r.ok).toBe(false);
    expect(r.checks.some((c) => c.name === "settlement-backend" && !c.ok)).toBe(true);
    rmSync(dir, { recursive: true });
  });

  it("fails when live mode is on but reservePolicy=off (explicit escape hatch)", async () => {
    const dir = mkdtempSync(join(tmpdir(), "gl-"));
    seedFreshReport(dir);
    const cfg = {
      ...DEFAULT_CONFIG,
      workspacePath: dir,
      minerAddress: SIGNER,
      settlementMode: "live" as const,
      reservePolicy: "off" as const,
    };
    const r = await goLive(cfg, {
      stateDir: dir,
      network: false,
      balanceProvider: provider({
        ok: true,
        balance: { address: SIGNER, raw: 1n, decimal: "1", formattedANM: "0", reachable: true },
        observedChainId: "1",
        fetchedAt: new Date().toISOString(),
        cached: false,
      }),
    });
    expect(r.ok).toBe(false);
    expect(r.checks.some((c) => c.name === "reserve-policy" && !c.ok)).toBe(true);
    rmSync(dir, { recursive: true });
  });

  it("fails when signer balance is below reserve", async () => {
    const dir = mkdtempSync(join(tmpdir(), "gl-"));
    seedFreshReport(dir);
    const cfg = {
      ...DEFAULT_CONFIG,
      workspacePath: dir,
      minerAddress: SIGNER,
      settlementMode: "live" as const,
      reservePolicy: "strict" as const,
    };
    const r = await goLive(cfg, {
      stateDir: dir,
      network: false,
      balanceProvider: provider({
        ok: true,
        balance: { address: SIGNER, raw: 1n, decimal: "1", formattedANM: "0", reachable: true },
        observedChainId: "1",
        fetchedAt: new Date().toISOString(),
        cached: false,
      }),
      policy: { reserveBalanceRaw: 1_000_000n },
    });
    expect(r.ok).toBe(false);
    expect(r.checks.some((c) => c.name === "signer-balance" && !c.ok)).toBe(true);
    rmSync(dir, { recursive: true });
  });

  it("fails when balance lookup itself fails", async () => {
    const dir = mkdtempSync(join(tmpdir(), "gl-"));
    seedFreshReport(dir);
    const cfg = {
      ...DEFAULT_CONFIG,
      workspacePath: dir,
      minerAddress: SIGNER,
      settlementMode: "live" as const,
      reservePolicy: "strict" as const,
    };
    const r = await goLive(cfg, {
      stateDir: dir,
      network: false,
      balanceProvider: provider({
        ok: false,
        failureReason: "rpc-unavailable",
        message: "ECONNREFUSED",
        fetchedAt: new Date().toISOString(),
      }),
    });
    expect(r.ok).toBe(false);
    expect(r.checks.some((c) => c.name === "signer-balance" && !c.ok)).toBe(true);
    rmSync(dir, { recursive: true });
  });
});
