import { mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { describe, expect, it } from "vitest";

import { DEFAULT_CONFIG } from "../src/config.js";
import {
  LIVE_SUBMIT_ACK,
  payloadFromReceipt,
  submitLive,
} from "../src/live-settlement.js";
import {
  classifyReconcile,
  inspectAttempt,
  listPending,
  reconcilePending,
  summarizeReconcile,
} from "../src/settlement-reconcile.js";
import {
  type ConfirmationPoller,
  SettlementJournal,
  type SettlementAttempt,
} from "../src/settlement-engine.js";
import type { Signer } from "../src/wallet.js";
import type { BalanceLookup, BalanceProvider } from "../src/balance-provider.js";

const SIGNER = "anm1qpzry9x8gf2tvdw0s3jn54khce6mua7l";
const RECIPIENT = "anm1zzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzz";

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

class HashingSigner implements Signer {
  public readonly name = "hashing";
  public callCount = 0;
  public failNext: Error | null = null;
  constructor(private readonly hash = "0x" + "ab".repeat(20)) {}
  async sign() {
    this.callCount++;
    if (this.failNext) {
      const e = this.failNext;
      this.failNext = null;
      throw e;
    }
    return { txHash: this.hash };
  }
}

function confirmingPoller(headBlock = 1000n): ConfirmationPoller {
  return {
    async fetchReceipt() {
      return { status: "confirmed", blockNumber: headBlock };
    },
    async headBlockNumber() {
      return headBlock;
    },
  };
}

function pendingPoller(): ConfirmationPoller {
  return {
    async fetchReceipt() {
      return { status: "pending" };
    },
    async headBlockNumber() {
      return 1n;
    },
  };
}

function missingPoller(): ConfirmationPoller {
  return {
    async fetchReceipt() {
      return { status: "missing" };
    },
    async headBlockNumber() {
      return 1n;
    },
  };
}

describe("crash recovery — persist-before-broadcast", () => {
  it("a crash after pending_submission leaves a resumable record; reconcile drives it forward", async () => {
    const dir = mkdtempSync(join(tmpdir(), "rc-"));
    const cfg = { ...DEFAULT_CONFIG, chainId: "1", minerAddress: SIGNER };
    // Simulate "process killed between queue() and signer.sign()" by writing a
    // pending_submission record directly to the journal.
    const journal = new SettlementJournal(dir);
    const crashAttempt: SettlementAttempt = {
      id: "att-crash-1",
      receiptId: "rec-crash-1",
      idempotencyKey: "k",
      recipient: RECIPIENT,
      amountRaw: 1n,
      status: "pending_submission",
      createdAt: new Date().toISOString(),
      updatedAt: new Date().toISOString(),
      attempts: 0,
      decisions: [],
      attemptHash: "h",
    };
    journal.append(crashAttempt);
    journal.reload();
    // First reconcile pass with NO signer must surface the pending record as
    // stuck-pending — it must not be silently re-broadcast.
    const readOnly = await reconcilePending({
      stateDir: dir,
      rpcUrl: "http://fake",
      poller: pendingPoller(),
    });
    expect(readOnly.length).toBe(1);
    expect(readOnly[0].classification).toBe("stuck-pending");

    // Operator supplies a signer → reconcile resumes from durable state and
    // drives all the way to paid via confirmed.
    const signer = new HashingSigner();
    const withSigner = await reconcilePending({
      stateDir: dir,
      rpcUrl: "http://fake",
      signer,
      poller: confirmingPoller(),
    });
    expect(withSigner.length).toBe(1);
    expect(signer.callCount).toBe(1);
    expect(withSigner[0].classification).toBe("paid");

    rmSync(dir, { recursive: true });
  });
});

describe("crash recovery — broadcast-before-confirmation", () => {
  it("an attempt stuck in confirming with a known tx hash reconciles to paid when poller confirms", async () => {
    const dir = mkdtempSync(join(tmpdir(), "rc-"));
    const journal = new SettlementJournal(dir);
    journal.append({
      id: "att-bpc-1",
      receiptId: "rec-bpc-1",
      idempotencyKey: "k",
      recipient: RECIPIENT,
      amountRaw: 1n,
      status: "confirming",
      createdAt: new Date().toISOString(),
      updatedAt: new Date().toISOString(),
      attempts: 1,
      decisions: [],
      attemptHash: "h",
      txHash: "0x" + "cc".repeat(20),
    });
    // Reconcile iterates confirming→confirmed→paid in a single pass.
    const step1 = await reconcilePending({
      stateDir: dir,
      rpcUrl: "http://fake",
      poller: confirmingPoller(1001n),
      confirmationDepth: 1,
    });
    expect(step1[0].after).toBe("paid");
    expect(step1[0].classification).toBe("paid");
    // A second reconcile is a no-op (no in-flight settlements remain).
    const step2 = await reconcilePending({
      stateDir: dir,
      rpcUrl: "http://fake",
      poller: confirmingPoller(1001n),
      confirmationDepth: 1,
    });
    expect(step2.length).toBe(0);
    rmSync(dir, { recursive: true });
  });

  it("reconcile classifies a vanished tx (post-sighting) as `replaced`", async () => {
    const dir = mkdtempSync(join(tmpdir(), "rc-"));
    const journal = new SettlementJournal(dir);
    journal.append({
      id: "att-rep-1",
      receiptId: "rec-rep-1",
      idempotencyKey: "k",
      recipient: RECIPIENT,
      amountRaw: 1n,
      status: "confirming",
      createdAt: new Date().toISOString(),
      updatedAt: new Date().toISOString(),
      attempts: 1,
      decisions: [],
      attemptHash: "h",
      txHash: "0x" + "dd".repeat(20),
      confirmations: 3,
    });
    const r = await reconcilePending({
      stateDir: dir,
      rpcUrl: "http://fake",
      poller: missingPoller(),
    });
    expect(r[0].classification).toBe("replaced");
    rmSync(dir, { recursive: true });
  });
});

describe("idempotent rerun", () => {
  it("a paid attempt blocks a repeat submit-live (verify-live journal-idempotency guard); signer is not re-called", async () => {
    const dir = mkdtempSync(join(tmpdir(), "rc-"));
    const cfg = { ...DEFAULT_CONFIG, chainId: "1", minerAddress: SIGNER };
    const signer = new HashingSigner();
    const payload = payloadFromReceipt("rec-idem-1", RECIPIENT, 1n, "artifact-hash");
    // First call: drives all the way to paid.
    const first = await submitLive(cfg, payload, {
      stateDir: dir,
      signer,
      acknowledgement: LIVE_SUBMIT_ACK,
      balanceProvider: provider(okBalance(1_000_000_000_000_000_000n)),
      poller: confirmingPoller(),
    });
    expect(first.attempt.status).toBe("paid");
    expect(signer.callCount).toBe(1);
    // Second call with same payload: verify-live's journal-idempotency check
    // refuses (prior paid attempt). The signer must NOT be called.
    let refused: unknown;
    try {
      await submitLive(cfg, payload, {
        stateDir: dir,
        signer,
        acknowledgement: LIVE_SUBMIT_ACK,
        balanceProvider: provider(okBalance(1_000_000_000_000_000_000n)),
        poller: confirmingPoller(),
      });
    } catch (err) {
      refused = err;
    }
    expect(refused).toBeTruthy();
    expect(signer.callCount).toBe(1); // unchanged — no double-spend
    rmSync(dir, { recursive: true });
  });
});

describe("restart reconciliation", () => {
  it("listPending returns only non-terminal attempts and excludes paid/rejected", async () => {
    const dir = mkdtempSync(join(tmpdir(), "rc-"));
    const journal = new SettlementJournal(dir);
    const now = new Date().toISOString();
    journal.append({
      id: "a-paid",
      receiptId: "rec-paid",
      idempotencyKey: "k",
      recipient: RECIPIENT,
      amountRaw: 1n,
      status: "paid",
      createdAt: now,
      updatedAt: now,
      attempts: 1,
      decisions: [],
      attemptHash: "h",
    });
    journal.append({
      id: "a-confirming",
      receiptId: "rec-conf",
      idempotencyKey: "k",
      recipient: RECIPIENT,
      amountRaw: 1n,
      status: "confirming",
      createdAt: now,
      updatedAt: now,
      attempts: 1,
      decisions: [],
      attemptHash: "h",
      txHash: "0xab",
    });
    const pending = listPending(dir);
    expect(pending.length).toBe(1);
    expect(pending[0].receiptId).toBe("rec-conf");
    rmSync(dir, { recursive: true });
  });
});

describe("inspectAttempt", () => {
  it("returns null for an unknown receipt", () => {
    const dir = mkdtempSync(join(tmpdir(), "ri-"));
    expect(inspectAttempt(dir, "nope")).toBeNull();
    rmSync(dir, { recursive: true });
  });

  it("returns a flattened history across all attempts", async () => {
    const dir = mkdtempSync(join(tmpdir(), "ri-"));
    const cfg = { ...DEFAULT_CONFIG, chainId: "1", minerAddress: SIGNER };
    const signer = new HashingSigner();
    const payload = payloadFromReceipt("rec-h-1", RECIPIENT, 1n, "a");
    await submitLive(cfg, payload, {
      stateDir: dir,
      signer,
      acknowledgement: LIVE_SUBMIT_ACK,
      balanceProvider: provider(okBalance(1_000_000_000_000_000_000n)),
      poller: confirmingPoller(),
    });
    const r = inspectAttempt(dir, "rec-h-1");
    expect(r).toBeTruthy();
    expect(r!.latest.status).toBe("paid");
    expect(r!.classification).toBe("paid");
    // The journal records at minimum: queued, submitted, confirming, confirmed, paid.
    expect(r!.history.length).toBeGreaterThan(0);
    rmSync(dir, { recursive: true });
  });
});

describe("classifyReconcile", () => {
  it("returns broadcast_pending_confirmation when txHash is known and status is submitted/confirming", () => {
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
    expect(
      classifyReconcile({ ...base, status: "submitted", txHash: "0xab" } as SettlementAttempt),
    ).toBe("broadcast_pending_confirmation");
    expect(
      classifyReconcile({ ...base, status: "confirming", txHash: "0xab" } as SettlementAttempt),
    ).toBe("broadcast_pending_confirmation");
    // Without a tx hash, we treat it as still-confirming (engine has not yet
    // observed a chain-side write).
    expect(classifyReconcile({ ...base, status: "confirming" } as SettlementAttempt)).toBe("still-confirming");
  });

  it("summarizeReconcile formats a breakdown", () => {
    const s = summarizeReconcile([
      { receiptId: "r1", attemptId: "a1", before: "confirming", after: "paid", classification: "paid", attempts: 1, updatedAt: "" },
      { receiptId: "r2", attemptId: "a2", before: "confirming", after: "confirming", classification: "broadcast_pending_confirmation", attempts: 1, updatedAt: "" },
    ]);
    expect(s).toMatch(/2 attempt/);
    expect(s).toMatch(/paid=1/);
    expect(s).toMatch(/broadcast_pending_confirmation=1/);
  });
});
