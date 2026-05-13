import { mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { describe, expect, it } from "vitest";

import type { BalanceLookup, BalanceProvider } from "../src/balance-provider.js";
import { DEFAULT_CONFIG } from "../src/config.js";
import { MetricsRegistry } from "../src/metrics.js";
import {
  LIVE_SUBMIT_ACK,
  payloadFromReceipt,
  submitLive,
} from "../src/live-settlement.js";
import {
  reconcilePending,
  type ReconcileEntry,
} from "../src/settlement-reconcile.js";
import {
  type ConfirmationPoller,
  SettlementJournal,
  type SettlementAttempt,
} from "../src/settlement-engine.js";
import type { Signer } from "../src/wallet.js";

const SIGNER = "anm1qpzry9x8gf2tvdw0s3jn54khce6mua7l";
const RECIPIENT = "anm1zzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzz";

class HashingSigner implements Signer {
  public readonly name = "hashing";
  async sign() {
    return { txHash: "0x" + "22".repeat(20) };
  }
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

function rejectingPoller(): ConfirmationPoller {
  return {
    async fetchReceipt() {
      return { status: "rejected", blockNumber: 100n };
    },
    async headBlockNumber() {
      return 100n;
    },
  };
}

describe("Metrics emission across live payout transitions", () => {
  it("submitLive increments payouts_broadcast + payouts_confirmed for a successful payout", async () => {
    const dir = mkdtempSync(join(tmpdir(), "mp-"));
    const cfg = { ...DEFAULT_CONFIG, chainId: "1", minerAddress: SIGNER };
    const metrics = new MetricsRegistry();
    await submitLive(cfg, payloadFromReceipt("rec-m1", RECIPIENT, 1n, "h"), {
      stateDir: dir,
      signer: new HashingSigner(),
      acknowledgement: LIVE_SUBMIT_ACK,
      balanceProvider: provider(okBalance(1_000_000_000_000_000_000n)),
      poller: confirmingPoller(),
      metrics,
    });
    const c = metrics.snapshotCounters();
    expect(c.payouts_broadcast).toBeGreaterThanOrEqual(1);
    expect(c.payouts_confirmed).toBeGreaterThanOrEqual(1);
    rmSync(dir, { recursive: true });
  });

  it("submitLive increments payouts_broadcast + payouts_rejected when chain rejects the tx", async () => {
    const dir = mkdtempSync(join(tmpdir(), "mp-"));
    const cfg = { ...DEFAULT_CONFIG, chainId: "1", minerAddress: SIGNER };
    const metrics = new MetricsRegistry();
    await submitLive(cfg, payloadFromReceipt("rec-m2", RECIPIENT, 1n, "h"), {
      stateDir: dir,
      signer: new HashingSigner(),
      acknowledgement: LIVE_SUBMIT_ACK,
      balanceProvider: provider(okBalance(1_000_000_000_000_000_000n)),
      poller: rejectingPoller(),
      metrics,
    });
    const c = metrics.snapshotCounters();
    expect(c.payouts_broadcast).toBeGreaterThanOrEqual(1);
    expect(c.payouts_rejected).toBeGreaterThanOrEqual(1);
    expect(c.settlement_rejects).toBeGreaterThanOrEqual(1);
    rmSync(dir, { recursive: true });
  });
});

describe("Metrics survive across reconcile passes via persisted journal", () => {
  it("metrics snapshot reflects journal state regardless of in-memory counters", async () => {
    const dir = mkdtempSync(join(tmpdir(), "mp-"));
    // Seed a paid attempt directly to the journal.
    const journal = new SettlementJournal(dir);
    const att: SettlementAttempt = {
      id: "att-paid",
      receiptId: "rec-paid",
      idempotencyKey: "k",
      recipient: RECIPIENT,
      amountRaw: 50n,
      status: "paid",
      createdAt: new Date().toISOString(),
      updatedAt: new Date().toISOString(),
      attempts: 1,
      decisions: [],
      attemptHash: "h",
      txHash: "0xaa",
    };
    journal.append(att);
    const m = new MetricsRegistry();
    const snap = m.snapshot(dir);
    // Counters are zero (fresh process) but the journal-derived view shows
    // settlements by status.
    expect(snap.counters.payouts_confirmed).toBe(0);
    expect(snap.settlements.byStatus.paid).toBe(1);
    expect(snap.settlements.paidTotalRaw).toBe(50n);
    rmSync(dir, { recursive: true });
  });

  it("a reconcile pass increments payouts_confirmed via the engine's transition observer", async () => {
    const dir = mkdtempSync(join(tmpdir(), "mp-"));
    const journal = new SettlementJournal(dir);
    journal.append({
      id: "att-conf",
      receiptId: "rec-conf",
      idempotencyKey: "k",
      recipient: RECIPIENT,
      amountRaw: 10n,
      status: "confirming",
      createdAt: new Date().toISOString(),
      updatedAt: new Date().toISOString(),
      attempts: 1,
      decisions: [],
      attemptHash: "h",
      txHash: "0xbb",
    });
    const metrics = new MetricsRegistry();
    const entries: ReconcileEntry[] = await reconcilePending({
      stateDir: dir,
      rpcUrl: "http://fake",
      poller: confirmingPoller(),
      onTransition: (rec) => {
        if (rec.status === "paid") metrics.inc("payouts_confirmed");
        if (rec.status === "rejected") metrics.inc("payouts_rejected");
      },
    });
    expect(entries.length).toBe(1);
    expect(entries[0].after).toBe("paid");
    expect(metrics.snapshotCounters().payouts_confirmed).toBeGreaterThanOrEqual(1);
    rmSync(dir, { recursive: true });
  });
});
