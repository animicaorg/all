import { describe, expect, it } from "vitest";
import { getModel } from "@/server/ai/catalog";
import { customerCost, grossMargin } from "@/server/ai/pricing";

describe("pricing", () => {
  it("computes customer cost from per-1k prices", () => {
    const model = getModel("anm-fast-8b")!;
    // 1000 in @ 0.0002 + 2000 out @ 0.0005 = 0.0002 + 0.001 = 0.0012
    expect(customerCost(model, 1000, 2000)).toBe("0.0012");
  });

  it("prices embeddings on input only", () => {
    const model = getModel("anm-embed")!;
    // 5000 in @ 0.00002 = 0.0001 ; output price unused
    expect(customerCost(model, 5000, 0)).toBe("0.0001");
  });

  it("margin = customer − provider", () => {
    expect(grossMargin("0.0012", "0.0000003")).toBe("0.0011997");
  });

  it("zero tokens cost zero", () => {
    const model = getModel("anm-pro-70b")!;
    expect(customerCost(model, 0, 0)).toBe("0");
  });
});
