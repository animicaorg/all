import { mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { describe, expect, it } from "vitest";

import { MetricsRegistry } from "../src/metrics.js";

describe("MetricsRegistry", () => {
  it("counters increment and reset", () => {
    const r = new MetricsRegistry();
    r.inc("jobs_discovered");
    r.inc("jobs_discovered");
    r.inc("settlement_attempts", 3);
    const s = r.snapshotCounters();
    expect(s.jobs_discovered).toBe(2);
    expect(s.settlement_attempts).toBe(3);
  });
  it("snapshot reads from disk and groups by status", () => {
    const dir = mkdtempSync(join(tmpdir(), "metr-"));
    const r = new MetricsRegistry();
    const s = r.snapshot(dir);
    expect(s.jobs.total).toBe(0);
    expect(s.settlements.total).toBe(0);
    expect(s.revenue.byAddress).toEqual([]);
    expect(s.generatedAt).toBeDefined();
    rmSync(dir, { recursive: true });
  });
});
