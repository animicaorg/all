import { mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { describe, expect, it } from "vitest";

import { BillingEngine, OfflineSettlement } from "../src/billing.js";
import { DEFAULT_CONFIG } from "../src/config.js";

describe("BillingEngine idempotency", () => {
  it("returns the same receipt when charge is invoked twice with the same idempotencyKey", async () => {
    const dir = mkdtempSync(join(tmpdir(), "billing-idem-"));
    const engine = new BillingEngine(dir, { ...DEFAULT_CONFIG }, undefined, new OfflineSettlement());
    const est = engine.authorize({ kind: "code-task" });

    const first = await engine.charge({
      kind: "code-task",
      estimate: est,
      status: "estimated",
      idempotencyKey: "session-1:task-A",
    });
    const second = await engine.charge({
      kind: "code-task",
      estimate: est,
      status: "estimated",
      idempotencyKey: "session-1:task-A",
    });
    expect(second.id).toBe(first.id);
    expect(second.receiptHash).toBe(first.receiptHash);

    // Spending counter should only have advanced once.
    expect(engine.getBudget().sessionSpentRaw).toBe(est.raw);

    // A different key DOES create a new receipt.
    const third = await engine.charge({
      kind: "code-task",
      estimate: est,
      status: "estimated",
      idempotencyKey: "session-1:task-B",
    });
    expect(third.id).not.toBe(first.id);

    rmSync(dir, { recursive: true });
  });
});
