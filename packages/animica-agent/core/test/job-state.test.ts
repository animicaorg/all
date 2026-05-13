import { mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { describe, expect, it } from "vitest";

import { canTransition, classifyJobFailure, InvalidTransition, JobStateStore, TERMINAL_STATUSES } from "../src/job-state.js";

describe("job-state transitions", () => {
  it("allows the documented happy-path moves", () => {
    expect(canTransition("discovered", "accepted")).toBe(true);
    expect(canTransition("accepted", "running")).toBe(true);
    expect(canTransition("running", "completed")).toBe(true);
    expect(canTransition("completed", "artifacted")).toBe(true);
    expect(canTransition("artifacted", "submitted")).toBe(true);
    expect(canTransition("submitted", "accepted_remote")).toBe(true);
    expect(canTransition("accepted_remote", "settlement_pending")).toBe(true);
    expect(canTransition("settlement_pending", "paid")).toBe(true);
  });
  it("forbids out-of-order moves", () => {
    expect(canTransition("discovered", "running")).toBe(false);
    expect(canTransition("running", "submitted")).toBe(false);
    expect(canTransition("paid", "running")).toBe(false);
  });
  it("permits the recovery edge from accepted/running back to discovered (and only those)", () => {
    expect(canTransition("accepted", "discovered")).toBe(true);
    expect(canTransition("running", "discovered")).toBe(true);
    expect(canTransition("artifacted", "discovered")).toBe(false);
    expect(canTransition("submitted", "discovered")).toBe(false);
  });
  it("treats same-status as idempotent (allowed)", () => {
    expect(canTransition("running", "running")).toBe(true);
  });
  it("terminals have no outbound edges", () => {
    for (const t of TERMINAL_STATUSES) {
      for (const u of ["discovered", "accepted", "running", "completed", "artifacted", "submitted"] as const) {
        if (t === u) continue;
        expect(canTransition(t, u)).toBe(false);
      }
    }
  });
});

describe("JobStateStore", () => {
  it("discover is idempotent for the same jobId", () => {
    const dir = mkdtempSync(join(tmpdir(), "jss-"));
    const store = new JobStateStore(dir);
    const a = store.discover({ jobId: "j1", idempotencyKey: "k1" });
    const b = store.discover({ jobId: "j1", idempotencyKey: "k1" });
    expect(a.jobId).toBe(b.jobId);
    expect(store.list()).toHaveLength(1);
    rmSync(dir, { recursive: true });
  });

  it("transition rejects illegal moves and accepts legal ones", () => {
    const dir = mkdtempSync(join(tmpdir(), "jss-"));
    const store = new JobStateStore(dir);
    store.discover({ jobId: "j1", idempotencyKey: "k1" });
    expect(() => store.transition("j1", "running")).toThrowError(InvalidTransition);
    const r = store.transition("j1", "accepted");
    expect(r.status).toBe("accepted");
    const r2 = store.transition("j1", "running");
    expect(r2.status).toBe("running");
    expect(r2.attempts).toBe(1);
    // re-entering running should bump attempts again.
    const r3 = store.transition("j1", "running");
    expect(r3.attempts).toBe(2);
    rmSync(dir, { recursive: true });
  });

  it("survives restart by reloading the JSONL journal", () => {
    const dir = mkdtempSync(join(tmpdir(), "jss-"));
    const a = new JobStateStore(dir);
    a.discover({ jobId: "j1", idempotencyKey: "k1" });
    a.transition("j1", "accepted");
    a.transition("j1", "running");
    // simulate process restart
    const b = new JobStateStore(dir);
    const rec = b.get("j1");
    expect(rec?.status).toBe("running");
    expect(rec?.attempts).toBe(1);
    rmSync(dir, { recursive: true });
  });

  it("compact reduces a busy journal to one line per jobId", () => {
    const dir = mkdtempSync(join(tmpdir(), "jss-"));
    const store = new JobStateStore(dir);
    store.discover({ jobId: "j1", idempotencyKey: "k1" });
    store.transition("j1", "accepted");
    store.transition("j1", "running");
    const path = store.path();
    const beforeLines = readFileSync(path, "utf8").split(/\r?\n/).filter(Boolean).length;
    expect(beforeLines).toBe(3);
    const dropped = store.compact();
    expect(dropped).toBeGreaterThan(0);
    const afterLines = readFileSync(path, "utf8").split(/\r?\n/).filter(Boolean).length;
    expect(afterLines).toBe(1);
    rmSync(dir, { recursive: true });
  });

  it("tolerates a corrupt line without losing earlier records", () => {
    const dir = mkdtempSync(join(tmpdir(), "jss-"));
    const a = new JobStateStore(dir);
    a.discover({ jobId: "j1", idempotencyKey: "k1" });
    // Append a garbage line that safeParse will reject.
    writeFileSync(a.path(), readFileSync(a.path(), "utf8") + "this is not json\n", { encoding: "utf8" });
    const b = new JobStateStore(dir);
    expect(b.get("j1")?.status).toBe("discovered");
    rmSync(dir, { recursive: true });
  });
});

describe("classifyJobFailure", () => {
  it("classifies common patterns", () => {
    expect(classifyJobFailure("insufficient balance")).toBe("permanent");
    expect(classifyJobFailure("chainId mismatch")).toBe("permanent");
    expect(classifyJobFailure("ETIMEDOUT")).toBe("transient");
    expect(classifyJobFailure("503 Service Unavailable")).toBe("transient");
    expect(classifyJobFailure(undefined)).toBe("unknown");
    expect(classifyJobFailure("something random")).toBe("unknown");
  });
});
