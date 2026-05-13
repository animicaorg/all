import { appendFileSync, mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { describe, expect, it } from "vitest";

import type { BalanceLookup, BalanceProvider } from "../src/balance-provider.js";
import {
  checkCoordinatorFreshness,
  latestVerificationReport,
} from "../src/coordinator-verify.js";
import { DEFAULT_CONFIG } from "../src/config.js";
import {
  LIVE_SUBMIT_ACK,
  LiveSubmitRefused,
  payloadFromReceipt,
  submitLive,
} from "../src/live-settlement.js";
import type { ConfirmationPoller } from "../src/settlement-engine.js";
import { safeStringify } from "../src/safe-json.js";
import type { Signer } from "../src/wallet.js";

const SIGNER = "anm1qpzry9x8gf2tvdw0s3jn54khce6mua7l";
const RECIPIENT = "anm1zzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzz";

class HashingSigner implements Signer {
  public readonly name = "hashing";
  public called = 0;
  async sign() {
    this.called++;
    return { txHash: "0x" + "11".repeat(20) };
  }
}

function confirmingPoller(): ConfirmationPoller {
  return {
    async fetchReceipt() {
      return { status: "confirmed", blockNumber: 100n };
    },
    async headBlockNumber() {
      return 100n;
    },
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
    balance: { address: SIGNER, raw, decimal: raw.toString(), formattedANM: "0", reachable: true },
    observedChainId: "1",
    fetchedAt: new Date().toISOString(),
    cached: false,
  };
}

function seedReport(stateDir: string, generatedAt: Date, ok: boolean, baseUrl = "http://coord") {
  const rec = {
    id: "rep-" + generatedAt.toISOString(),
    baseUrl,
    generatedAt: generatedAt.toISOString(),
    ok,
    checks: [],
    summary: ok ? "ok" : "fail",
  };
  appendFileSync(join(stateDir, "coordinator-verifications.jsonl"), safeStringify(rec) + "\n", "utf8");
}

describe("checkCoordinatorFreshness", () => {
  it("returns fresh=false when no reports exist", () => {
    const dir = mkdtempSync(join(tmpdir(), "cf-"));
    const r = checkCoordinatorFreshness({ stateDir: dir });
    expect(r.fresh).toBe(false);
    expect(r.reason).toBe("no-report");
    rmSync(dir, { recursive: true });
  });

  it("returns fresh=true when a recent ok report exists", () => {
    const dir = mkdtempSync(join(tmpdir(), "cf-"));
    seedReport(dir, new Date(), true);
    const r = checkCoordinatorFreshness({ stateDir: dir });
    expect(r.fresh).toBe(true);
    rmSync(dir, { recursive: true });
  });

  it("returns fresh=false when latest is not ok", () => {
    const dir = mkdtempSync(join(tmpdir(), "cf-"));
    seedReport(dir, new Date(), false);
    const r = checkCoordinatorFreshness({ stateDir: dir });
    expect(r.fresh).toBe(false);
    expect(r.reason).toBe("latest-not-ok");
    rmSync(dir, { recursive: true });
  });

  it("returns fresh=false when latest ok report is older than window", () => {
    const dir = mkdtempSync(join(tmpdir(), "cf-"));
    seedReport(dir, new Date(Date.now() - 48 * 60 * 60_000), true);
    const r = checkCoordinatorFreshness({ stateDir: dir, windowMs: 24 * 60 * 60_000 });
    expect(r.fresh).toBe(false);
    expect(r.reason).toBe("latest-stale");
    rmSync(dir, { recursive: true });
  });

  it("filters by baseUrl when supplied", () => {
    const dir = mkdtempSync(join(tmpdir(), "cf-"));
    seedReport(dir, new Date(), true, "http://other");
    const r = checkCoordinatorFreshness({ stateDir: dir, baseUrl: "http://target" });
    expect(r.fresh).toBe(false);
    expect(r.reason).toBe("no-report");
    rmSync(dir, { recursive: true });
  });

  it("uses the most recent ok report even if a later report is failed", () => {
    const dir = mkdtempSync(join(tmpdir(), "cf-"));
    seedReport(dir, new Date(Date.now() - 1000), true);
    seedReport(dir, new Date(), false);
    const r = checkCoordinatorFreshness({ stateDir: dir });
    // Latest non-ok still flips fresh to false.
    expect(r.fresh).toBe(false);
    expect(r.reason).toBe("latest-not-ok");
    rmSync(dir, { recursive: true });
  });
});

describe("latestVerificationReport", () => {
  it("returns the most recent report", () => {
    const dir = mkdtempSync(join(tmpdir(), "lv-"));
    seedReport(dir, new Date(Date.now() - 1000), false);
    seedReport(dir, new Date(), true);
    const r = latestVerificationReport(dir);
    expect(r?.ok).toBe(true);
    rmSync(dir, { recursive: true });
  });
});

describe("submitLive with requireFreshCoordinator gate", () => {
  it("refuses when no fresh coordinator verification exists", async () => {
    const dir = mkdtempSync(join(tmpdir(), "lv-"));
    const cfg = { ...DEFAULT_CONFIG, chainId: "1", minerAddress: SIGNER };
    const signer = new HashingSigner();
    let caught: unknown;
    try {
      await submitLive(cfg, payloadFromReceipt("rec-1", RECIPIENT, 1n, "h"), {
        stateDir: dir,
        signer,
        acknowledgement: LIVE_SUBMIT_ACK,
        balanceProvider: provider(okBalance(1_000_000_000_000_000_000n)),
        poller: confirmingPoller(),
        requireFreshCoordinator: { windowMs: 60_000 },
      });
    } catch (err) {
      caught = err;
    }
    expect(caught).toBeInstanceOf(LiveSubmitRefused);
    expect((caught as LiveSubmitRefused).reason).toBe("coordinator-stale");
    expect(signer.called).toBe(0);
    rmSync(dir, { recursive: true });
  });

  it("proceeds when a fresh ok coordinator verification exists", async () => {
    const dir = mkdtempSync(join(tmpdir(), "lv-"));
    seedReport(dir, new Date(), true);
    const cfg = { ...DEFAULT_CONFIG, chainId: "1", minerAddress: SIGNER };
    const signer = new HashingSigner();
    const r = await submitLive(cfg, payloadFromReceipt("rec-2", RECIPIENT, 1n, "h"), {
      stateDir: dir,
      signer,
      acknowledgement: LIVE_SUBMIT_ACK,
      balanceProvider: provider(okBalance(1_000_000_000_000_000_000n)),
      poller: confirmingPoller(),
      requireFreshCoordinator: { windowMs: 60_000 },
    });
    expect(r.attempt.status).toBe("paid");
    expect(signer.called).toBe(1);
    rmSync(dir, { recursive: true });
  });
});
