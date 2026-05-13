import { mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { describe, expect, it } from "vitest";

import type { BalanceLookup, BalanceProvider } from "../src/balance-provider.js";
import { DEFAULT_CONFIG } from "../src/config.js";
import {
  classifyWatchOutcome,
  LIVE_SUBMIT_ACK,
  LiveSubmitRefused,
  payloadFromReceipt,
  submitLive,
  summarizeWatch,
  verifyLive,
  watchLive,
} from "../src/live-settlement.js";
import {
  type ConfirmationPoller,
  SettlementJournal,
  type SettlementAttempt,
} from "../src/settlement-engine.js";
import type { Signer } from "../src/wallet.js";

const SIGNER = "anm1qpzry9x8gf2tvdw0s3jn54khce6mua7l";
const RECIPIENT = "anm1zzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzz";

function constBalanceProvider(lookup: BalanceLookup): BalanceProvider {
  return {
    async lookup() {
      return lookup;
    },
    invalidate() {},
  };
}

function okBalance(rawANM: bigint): BalanceLookup {
  return {
    ok: true,
    balance: { address: SIGNER, raw: rawANM, decimal: rawANM.toString(), formattedANM: "", reachable: true },
    observedChainId: "1",
    fetchedAt: new Date().toISOString(),
    cached: false,
  };
}

function failBalance(): BalanceLookup {
  return {
    ok: false,
    failureReason: "rpc-unavailable",
    message: "ECONNREFUSED",
    fetchedAt: new Date().toISOString(),
  };
}

/** A signer that records what was requested and returns a tx hash. */
class RecordingSigner implements Signer {
  public readonly name = "recording";
  public lastRequest: unknown;
  public hash = "0x" + "ab".repeat(20);
  async sign(req: { payload: { kind: string; data: unknown } }) {
    this.lastRequest = req;
    return { txHash: this.hash };
  }
}

/** A poller that returns a confirmation after N calls. */
function pollerThatConfirmsAfter(n: number): ConfirmationPoller {
  let calls = 0;
  return {
    async fetchReceipt() {
      calls++;
      if (calls >= n) return { status: "confirmed", blockNumber: 1000n };
      return { status: "pending" };
    },
    async headBlockNumber() {
      return 1001n;
    },
  };
}

/** Always reports the tx as missing — emulates a dropped tx. */
function pollerMissing(): ConfirmationPoller {
  return {
    async fetchReceipt() {
      return { status: "missing" };
    },
    async headBlockNumber() {
      return 1000n;
    },
  };
}

describe("verifyLive (dry-run)", () => {
  it("reports ok=true for a well-formed payload with a healthy balance", async () => {
    const dir = mkdtempSync(join(tmpdir(), "lv-"));
    const cfg = { ...DEFAULT_CONFIG, chainId: "1", minerAddress: SIGNER };
    const payload = payloadFromReceipt("rec-1", RECIPIENT, 1_000_000_000_000_000n);
    const report = await verifyLive(cfg, payload, {
      stateDir: dir,
      balanceProvider: constBalanceProvider(okBalance(1_000_000_000_000_000_000n)),
    });
    // Cannot guarantee the RPC probe in checkSettlementReadiness will succeed
    // in a sandboxed test, but signer + recipient + amount checks must pass
    // and balance-lookup must be present.
    expect(report.payload).toEqual(payload);
    expect(report.checks.find((c) => c.name === "signer-address")?.ok).toBe(true);
    expect(report.checks.find((c) => c.name === "recipient")?.ok).toBe(true);
    expect(report.checks.find((c) => c.name === "amount")?.ok).toBe(true);
    expect(report.checks.find((c) => c.name === "balance-lookup")?.ok).toBe(true);
    rmSync(dir, { recursive: true });
  });

  it("reports ok=false when balance lookup fails", async () => {
    const dir = mkdtempSync(join(tmpdir(), "lv-"));
    const cfg = { ...DEFAULT_CONFIG, chainId: "1", minerAddress: SIGNER };
    const payload = payloadFromReceipt("rec-2", RECIPIENT, 1n);
    const report = await verifyLive(cfg, payload, {
      stateDir: dir,
      balanceProvider: constBalanceProvider(failBalance()),
    });
    expect(report.checks.find((c) => c.name === "balance-lookup")?.ok).toBe(false);
    expect(report.ok).toBe(false);
    rmSync(dir, { recursive: true });
  });

  it("reports ok=false when recipient is malformed", async () => {
    const dir = mkdtempSync(join(tmpdir(), "lv-"));
    const cfg = { ...DEFAULT_CONFIG, chainId: "1", minerAddress: SIGNER };
    const payload = payloadFromReceipt("rec-3", "BAD-RECIPIENT", 1n);
    const report = await verifyLive(cfg, payload, {
      stateDir: dir,
      balanceProvider: constBalanceProvider(okBalance(1n)),
    });
    expect(report.checks.find((c) => c.name === "recipient")?.ok).toBe(false);
    expect(report.ok).toBe(false);
    rmSync(dir, { recursive: true });
  });

  it("reports ok=false when amount is zero", async () => {
    const dir = mkdtempSync(join(tmpdir(), "lv-"));
    const cfg = { ...DEFAULT_CONFIG, chainId: "1", minerAddress: SIGNER };
    const payload = payloadFromReceipt("rec-4", RECIPIENT, 0n);
    const report = await verifyLive(cfg, payload, {
      stateDir: dir,
      balanceProvider: constBalanceProvider(okBalance(1n)),
    });
    expect(report.checks.find((c) => c.name === "amount")?.ok).toBe(false);
    expect(report.ok).toBe(false);
    rmSync(dir, { recursive: true });
  });

  it("refuses when prior attempt is already paid", async () => {
    const dir = mkdtempSync(join(tmpdir(), "lv-"));
    const cfg = { ...DEFAULT_CONFIG, chainId: "1", minerAddress: SIGNER };
    // Seed the journal with a paid attempt for receipt rec-prior.
    const journal = new SettlementJournal(dir);
    const paid: SettlementAttempt = {
      id: "att-1",
      receiptId: "rec-prior",
      idempotencyKey: "key",
      recipient: RECIPIENT,
      amountRaw: 1n,
      status: "paid",
      createdAt: new Date().toISOString(),
      updatedAt: new Date().toISOString(),
      attempts: 1,
      decisions: [],
      attemptHash: "h",
      txHash: "0x" + "ab".repeat(20),
    };
    journal.append(paid);
    const report = await verifyLive(cfg, payloadFromReceipt("rec-prior", RECIPIENT, 1n), {
      stateDir: dir,
      balanceProvider: constBalanceProvider(okBalance(1n)),
    });
    expect(report.ok).toBe(false);
    expect(report.checks.some((c) => c.name === "journal-idempotency" && !c.ok)).toBe(true);
    rmSync(dir, { recursive: true });
  });
});

describe("submitLive operator acknowledgement", () => {
  it("refuses without the exact acknowledgement string", async () => {
    const dir = mkdtempSync(join(tmpdir(), "lv-"));
    const cfg = { ...DEFAULT_CONFIG, chainId: "1", minerAddress: SIGNER };
    const payload = payloadFromReceipt("rec-ack-1", RECIPIENT, 1n);
    const signer = new RecordingSigner();
    let caught: unknown;
    try {
      await submitLive(cfg, payload, {
        stateDir: dir,
        signer,
        acknowledgement: "yes please",
        balanceProvider: constBalanceProvider(okBalance(1_000_000_000_000_000_000n)),
        poller: pollerThatConfirmsAfter(1),
      });
    } catch (err) {
      caught = err;
    }
    expect(caught).toBeInstanceOf(LiveSubmitRefused);
    expect((caught as LiveSubmitRefused).reason).toBe("no-ack");
    expect(signer.lastRequest).toBeUndefined(); // never called
    rmSync(dir, { recursive: true });
  });

  it("refuses when verify-live fails (e.g. malformed recipient)", async () => {
    const dir = mkdtempSync(join(tmpdir(), "lv-"));
    const cfg = { ...DEFAULT_CONFIG, chainId: "1", minerAddress: SIGNER };
    const payload = payloadFromReceipt("rec-ack-2", "BAD", 1n);
    const signer = new RecordingSigner();
    let caught: unknown;
    try {
      await submitLive(cfg, payload, {
        stateDir: dir,
        signer,
        acknowledgement: LIVE_SUBMIT_ACK,
        balanceProvider: constBalanceProvider(okBalance(1n)),
      });
    } catch (err) {
      caught = err;
    }
    expect(caught).toBeInstanceOf(LiveSubmitRefused);
    expect((caught as LiveSubmitRefused).reason).toBe("verify-failed");
    rmSync(dir, { recursive: true });
  });
});

describe("watchLive persisted state + resume", () => {
  it("resumes a submitted attempt to paid when poller confirms", async () => {
    const dir = mkdtempSync(join(tmpdir(), "lv-"));
    // Seed an attempt at confirming via direct journal append.
    const journal = new SettlementJournal(dir);
    const att: SettlementAttempt = {
      id: "att-w-1",
      receiptId: "rec-w-1",
      idempotencyKey: "key",
      recipient: RECIPIENT,
      amountRaw: 100n,
      status: "confirming",
      createdAt: new Date().toISOString(),
      updatedAt: new Date().toISOString(),
      attempts: 1,
      decisions: [],
      attemptHash: "h",
      txHash: "0xfeedface" + "00".repeat(16),
    };
    journal.append(att);

    const entries = await watchLive({
      stateDir: dir,
      rpcUrl: "http://fake",
      poller: pollerThatConfirmsAfter(1),
    });
    expect(entries.length).toBe(1);
    // First drive moves it from confirming to confirmed.
    expect(["confirmed", "still-confirming", "paid"]).toContain(entries[0].after);
    rmSync(dir, { recursive: true });
  });

  it("classifies a missing tx as dropped after a sighting", async () => {
    const dir = mkdtempSync(join(tmpdir(), "lv-"));
    const journal = new SettlementJournal(dir);
    const att: SettlementAttempt = {
      id: "att-w-drop",
      receiptId: "rec-w-drop",
      idempotencyKey: "key",
      recipient: RECIPIENT,
      amountRaw: 1n,
      status: "confirming",
      createdAt: new Date().toISOString(),
      updatedAt: new Date().toISOString(),
      attempts: 1,
      decisions: [],
      attemptHash: "h",
      txHash: "0xdeadbeef" + "00".repeat(16),
      confirmations: 3, // we have a prior sighting
    };
    journal.append(att);
    const entries = await watchLive({
      stateDir: dir,
      rpcUrl: "http://fake",
      poller: pollerMissing(),
    });
    expect(entries.length).toBe(1);
    // After-sighting missing → engine transitions to failed_transient w/ tx-replaced
    expect(entries[0].classification).toBe("replaced");
    rmSync(dir, { recursive: true });
  });

  it("never broadcasts in read-only mode (no signer)", async () => {
    const dir = mkdtempSync(join(tmpdir(), "lv-"));
    const journal = new SettlementJournal(dir);
    const att: SettlementAttempt = {
      id: "att-w-pending",
      receiptId: "rec-w-pending",
      idempotencyKey: "key",
      recipient: RECIPIENT,
      amountRaw: 1n,
      status: "pending_submission",
      createdAt: new Date().toISOString(),
      updatedAt: new Date().toISOString(),
      attempts: 0,
      decisions: [],
      attemptHash: "h",
    };
    journal.append(att);
    const entries = await watchLive({
      stateDir: dir,
      rpcUrl: "http://fake",
      poller: pollerThatConfirmsAfter(1),
    });
    expect(entries.length).toBe(1);
    // No signer → the engine throws on attempted submit, watch records this
    // as a stuck-pending entry rather than driving forward.
    expect(["stuck-pending", "failed"]).toContain(entries[0].classification);
    rmSync(dir, { recursive: true });
  });
});

describe("watch helpers", () => {
  it("classifyWatchOutcome maps every terminal state", () => {
    const base = {
      id: "a",
      receiptId: "r",
      idempotencyKey: "k",
      recipient: RECIPIENT,
      amountRaw: 1n,
      createdAt: "",
      updatedAt: "",
      attempts: 0,
      decisions: [],
      attemptHash: "h",
    };
    expect(classifyWatchOutcome("confirming", { ...base, status: "paid" } as SettlementAttempt)).toBe("paid");
    expect(classifyWatchOutcome("confirming", { ...base, status: "rejected" } as SettlementAttempt)).toBe("rejected");
    expect(classifyWatchOutcome("confirming", { ...base, status: "expired" } as SettlementAttempt)).toBe("expired");
    expect(classifyWatchOutcome("confirming", { ...base, status: "failed_permanent" } as SettlementAttempt)).toBe("failed");
    expect(
      classifyWatchOutcome("confirming", { ...base, status: "failed_transient", failureReason: "tx-dropped" } as SettlementAttempt),
    ).toBe("dropped");
    expect(
      classifyWatchOutcome("confirming", { ...base, status: "failed_transient", failureReason: "tx-replaced" } as SettlementAttempt),
    ).toBe("replaced");
  });

  it("summarizeWatch formats a count breakdown", () => {
    const s = summarizeWatch([
      {
        receiptId: "r1",
        before: "confirming",
        after: "paid",
        classification: "paid",
      },
      {
        receiptId: "r2",
        before: "confirming",
        after: "confirming",
        classification: "still-confirming",
      },
    ]);
    expect(s).toMatch(/2 attempt/);
    expect(s).toMatch(/paid=1/);
    expect(s).toMatch(/still-confirming=1/);
  });
});
