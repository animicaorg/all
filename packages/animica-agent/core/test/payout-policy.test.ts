import { mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { describe, expect, it } from "vitest";

import type { Receipt } from "../src/billing.js";
import { evaluatePayout, PayoutAuditor, PolicyPayoutGuard, DEFAULT_PAYOUT_POLICY } from "../src/payout-policy.js";
import type { SettlementAttempt } from "../src/settlement-engine.js";
import type { WalletBalance } from "../src/wallet.js";

const RECIPIENT = "anm1qpzry9x8gf2tvdw0s3jn54khce6mua7l";

function makeReceipt(overrides: Partial<Receipt> = {}): Receipt {
  return {
    id: "rec-1",
    at: new Date(Date.now() - 5 * 60 * 1000).toISOString(),
    kind: "scaffold",
    estimate: { raw: 1_000_000_000_000_000n, formattedANM: "0.001", tier: "base", breakdown: [] },
    status: "estimated",
    wallet: RECIPIENT,
    worker: "w1",
    receiptHash: "h".repeat(64),
    idempotencyKey: "useful-work:job1:abcdef",
    ...overrides,
  } as Receipt;
}

function makePaidAttempt(overrides: Partial<SettlementAttempt> = {}): SettlementAttempt {
  return {
    id: "a",
    receiptId: "rec-prior",
    idempotencyKey: "k",
    recipient: RECIPIENT,
    amountRaw: 100n,
    status: "paid",
    createdAt: new Date().toISOString(),
    updatedAt: new Date().toISOString(),
    attempts: 1,
    decisions: [],
    attemptHash: "h",
    ...overrides,
  };
}

describe("evaluatePayout", () => {
  it("allows a well-formed payout on the enforced chain", () => {
    const r = evaluatePayout({
      receipt: makeReceipt(),
      worker: "w1",
      recipient: RECIPIENT,
      amountRaw: 100n,
      artifactHash: "abcdef",
      chainId: "1",
    });
    expect(r.allowed).toBe(true);
  });

  it("refuses on a wrong chain id", () => {
    const r = evaluatePayout({
      receipt: makeReceipt(),
      worker: "w1",
      recipient: RECIPIENT,
      amountRaw: 100n,
      artifactHash: "abcdef",
      chainId: "99",
    });
    expect(r.allowed).toBe(false);
    expect(r.reason).toBe("policy-disabled-for-network");
  });

  it("refuses when amount exceeds receipt ceiling", () => {
    const r = evaluatePayout({
      receipt: makeReceipt(),
      recipient: RECIPIENT,
      amountRaw: 2_000_000_000_000_000n, // 0.002 > 0.001 receipt
      artifactHash: "x",
      chainId: "1",
    });
    expect(r.allowed).toBe(false);
    expect(r.reason).toBe("amount-exceeds-receipt");
  });

  it("refuses below maturity", () => {
    const r = evaluatePayout({
      receipt: makeReceipt({ at: new Date().toISOString() }),
      recipient: RECIPIENT,
      amountRaw: 1n,
      artifactHash: "x",
      chainId: "1",
    });
    expect(r.allowed).toBe(false);
    expect(r.reason).toBe("below-maturity");
  });

  it("refuses missing artifact hash by default", () => {
    const r = evaluatePayout({
      receipt: makeReceipt(),
      recipient: RECIPIENT,
      amountRaw: 1n,
      chainId: "1",
    });
    expect(r.allowed).toBe(false);
    expect(r.reason).toBe("missing-artifact-hash");
  });

  it("refuses duplicate receipt with a confirmed prior attempt", () => {
    const r = evaluatePayout({
      receipt: makeReceipt(),
      recipient: RECIPIENT,
      amountRaw: 1n,
      artifactHash: "x",
      chainId: "1",
      priorAttempts: [makePaidAttempt({ receiptId: "rec-1", status: "confirmed" })],
    });
    expect(r.allowed).toBe(false);
    expect(r.reason).toBe("duplicate-receipt");
  });

  it("refuses duplicate artifact paid via another receipt", () => {
    const r = evaluatePayout({
      receipt: makeReceipt(),
      recipient: RECIPIENT,
      amountRaw: 1n,
      artifactHash: "abcdef",
      chainId: "1",
      priorPaidReceipts: [
        {
          id: "rec-other",
          at: new Date().toISOString(),
          estimate: { raw: 1n, formattedANM: "0", tier: "base", breakdown: [] },
          idempotencyKey: "useful-work:jobX:abcdef",
        } as Receipt,
      ],
    });
    expect(r.allowed).toBe(false);
    expect(r.reason).toBe("duplicate-artifact");
  });

  it("refuses daily cap exceeded", () => {
    const r = evaluatePayout(
      {
        receipt: makeReceipt(),
        recipient: RECIPIENT,
        amountRaw: 5n,
        artifactHash: "x",
        chainId: "1",
        priorAttempts: [makePaidAttempt({ amountRaw: 100n, updatedAt: new Date().toISOString() })],
      },
      { dailyCapRaw: 50n },
    );
    expect(r.allowed).toBe(false);
    expect(r.reason).toBe("daily-cap-exceeded");
  });

  it("refuses reserve balance violation", () => {
    const balance: WalletBalance = {
      address: RECIPIENT,
      raw: 100n,
      decimal: "100",
      formattedANM: "0",
      reachable: true,
    };
    const r = evaluatePayout(
      {
        receipt: makeReceipt(),
        recipient: RECIPIENT,
        amountRaw: 60n,
        artifactHash: "x",
        chainId: "1",
        signerBalance: balance,
      },
      { reserveBalanceRaw: 50n, mandatoryArtifactHash: false },
    );
    expect(r.allowed).toBe(false);
    expect(r.reason).toBe("reserve-balance-violation");
  });

  it("bypass=true allows everything", () => {
    const r = evaluatePayout(
      {
        receipt: makeReceipt({ at: new Date().toISOString() }),
        recipient: "bad",
        amountRaw: 9_999_999_999_999_999_999n,
        chainId: "99",
      },
      { bypass: true },
    );
    expect(r.allowed).toBe(true);
  });
});

describe("PayoutAuditor + PolicyPayoutGuard", () => {
  it("records every decision with policyDigest and a rejection reason", () => {
    const dir = mkdtempSync(join(tmpdir(), "pp-"));
    const auditor = new PayoutAuditor(dir);
    const guard = new PolicyPayoutGuard({}, auditor);
    const allow = guard.decide({
      receipt: makeReceipt(),
      worker: "w1",
      recipient: RECIPIENT,
      amountRaw: 1n,
      artifactHash: "abc",
      chainId: "1",
    });
    expect(allow.allowed).toBe(true);
    const deny = guard.decide({
      receipt: makeReceipt({ at: new Date().toISOString() }),
      worker: "w1",
      recipient: RECIPIENT,
      amountRaw: 1n,
      artifactHash: "abc",
      chainId: "1",
    });
    expect(deny.allowed).toBe(false);
    const recents = auditor.recent();
    expect(recents.length).toBe(2);
    expect(recents.every((r) => typeof r.policyDigest === "string" && r.policyDigest.length === 16)).toBe(true);
    rmSync(dir, { recursive: true });
  });
});

describe("DEFAULT_PAYOUT_POLICY", () => {
  it("has non-zero caps and active duplicate defenses", () => {
    expect(DEFAULT_PAYOUT_POLICY.dailyCapRaw).toBeGreaterThan(0n);
    expect(DEFAULT_PAYOUT_POLICY.duplicateReceiptDefense).toBe(true);
    expect(DEFAULT_PAYOUT_POLICY.duplicateArtifactDefense).toBe(true);
    expect(DEFAULT_PAYOUT_POLICY.mandatoryArtifactHash).toBe(true);
  });
});
