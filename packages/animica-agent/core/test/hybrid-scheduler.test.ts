import { describe, expect, it } from "vitest";

import { DEFAULT_CONFIG } from "../src/config.js";
import { plan, resolveHybridMode, type HybridInputs } from "../src/hybrid-scheduler.js";

function inputs(over: Partial<HybridInputs> = {}): HybridInputs {
  return {
    totalThreads: 8,
    totalGpuSlots: 0,
    chainMinerActive: true,
    observedHashrate: 0,
    targetHashrate: 0,
    ...over,
  };
}

describe("hybrid-scheduler.plan", () => {
  it("chain-only allocates everything to chain mining", () => {
    const p = plan("chain-only", inputs());
    expect(p.chainWorkers).toBe(8);
    expect(p.usefulWorkers).toBe(0);
  });
  it("useful-only allocates everything to useful-work", () => {
    const p = plan("useful-only", inputs({ totalGpuSlots: 2 }));
    expect(p.chainWorkers).toBe(0);
    expect(p.usefulWorkers).toBe(8);
    expect(p.usefulGpuSlots).toBe(2);
  });
  it("hybrid-balanced splits roughly 50/50 with floor=1", () => {
    const p = plan("hybrid-balanced", inputs({ totalThreads: 6 }));
    expect(p.chainWorkers + p.usefulWorkers).toBe(6);
    expect(p.chainWorkers).toBeGreaterThanOrEqual(1);
    expect(p.usefulWorkers).toBeGreaterThanOrEqual(1);
  });
  it("hybrid-miner-priority caps useful-work to ~25%", () => {
    const p = plan("hybrid-miner-priority", inputs({ totalThreads: 8, observedHashrate: 100, targetHashrate: 100 }));
    expect(p.chainWorkers).toBe(6);
    expect(p.usefulWorkers).toBe(2);
  });
  it("hybrid-miner-priority pauses useful-work if hashrate < 80% of target", () => {
    const p = plan("hybrid-miner-priority", inputs({ totalThreads: 8, observedHashrate: 50, targetHashrate: 100 }));
    expect(p.chainWorkers).toBe(8);
    expect(p.usefulWorkers).toBe(0);
  });
  it("hybrid-useful-priority keeps chain mining at floor=1", () => {
    const p = plan("hybrid-useful-priority", inputs({ totalThreads: 8, totalGpuSlots: 4 }));
    expect(p.chainWorkers).toBe(1);
    expect(p.usefulWorkers).toBe(7);
    expect(p.usefulGpuSlots).toBe(4);
  });
});

describe("resolveHybridMode", () => {
  it("returns useful-only when no miner is active", () => {
    expect(resolveHybridMode({ ...DEFAULT_CONFIG, minerMode: "auto" }, false)).toBe("useful-only");
  });
  it("returns useful-only when minerMode=off", () => {
    expect(resolveHybridMode({ ...DEFAULT_CONFIG, minerMode: "off" }, true)).toBe("useful-only");
  });
  it("maps resourceMode to hybrid mode", () => {
    expect(resolveHybridMode({ ...DEFAULT_CONFIG, resourceMode: "miner-priority" }, true)).toBe("hybrid-miner-priority");
    expect(resolveHybridMode({ ...DEFAULT_CONFIG, resourceMode: "agent-priority" }, true)).toBe("hybrid-useful-priority");
    expect(resolveHybridMode({ ...DEFAULT_CONFIG, resourceMode: "balanced" }, true)).toBe("hybrid-balanced");
  });
});
