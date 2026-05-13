import { mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { describe, expect, it } from "vitest";

import { BillingEngine, DEFAULT_BUDGET, DEFAULT_PRICING, estimate, OfflineSettlement } from "../src/billing.js";
import { formatANM, parseANM } from "../src/wallet.js";
import { DEFAULT_CONFIG } from "../src/config.js";

describe("billing", () => {
  it("estimates a base-tier code task as the per-action fee", () => {
    const e = estimate({ kind: "code-task" });
    expect(e.tier).toBe("base");
    expect(e.raw).toBe(DEFAULT_PRICING.base.perAction);
  });

  it("applies the miner subsidy bps correctly", () => {
    const e = estimate({ kind: "code-task", minerSubsidized: true });
    expect(e.raw).toBeLessThan(DEFAULT_PRICING.base.perAction);
  });

  it("authorize throws when daily budget exceeded", () => {
    const dir = mkdtempSync(join(tmpdir(), "billing-"));
    const engine = new BillingEngine(dir, { ...DEFAULT_CONFIG }, undefined, new OfflineSettlement());
    engine.setBudget({ dailyMaxRaw: 1n });
    expect(() => engine.authorize({ kind: "code-task" })).toThrowError(/daily budget exceeded/);
    rmSync(dir, { recursive: true });
  });

  it("formatANM strips trailing zeros and parseANM round-trips", () => {
    expect(formatANM(parseANM("1.500"))).toBe("1.5");
    expect(formatANM(parseANM("0.000001"))).toBe("0.000001");
    expect(parseANM("1")).toBe(10n ** 18n);
  });

  it("charge produces a settled receipt and updates spent counters", async () => {
    const dir = mkdtempSync(join(tmpdir(), "billing-"));
    const engine = new BillingEngine(dir, { ...DEFAULT_CONFIG }, undefined, new OfflineSettlement());
    const est = engine.authorize({ kind: "code-task" });
    const r = await engine.charge({ kind: "code-task", estimate: est, status: "estimated" });
    expect(r.status).toBe("settled");
    expect(r.signature).toBeDefined();
    expect(engine.getBudget().sessionSpentRaw).toBe(est.raw);
    rmSync(dir, { recursive: true });
  });

  it("default budgets are non-zero", () => {
    expect(DEFAULT_BUDGET.dailyMaxRaw).toBeGreaterThan(0n);
  });
});
