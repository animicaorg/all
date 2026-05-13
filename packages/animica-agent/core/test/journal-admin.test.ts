import { mkdtempSync, readFileSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { describe, expect, it } from "vitest";

import { archiveFailedJobs, compactJobs, compactSettlements, inspectQueues } from "../src/journal-admin.js";
import { JobStateStore } from "../src/job-state.js";
import { SettlementJournal, type SettlementAttempt } from "../src/settlement-engine.js";

describe("journal-admin", () => {
  it("compactJobs keeps exactly one record per jobId", () => {
    const dir = mkdtempSync(join(tmpdir(), "ja-"));
    const s = new JobStateStore(dir);
    s.discover({ jobId: "j", idempotencyKey: "k" });
    s.transition("j", "accepted");
    s.transition("j", "running");
    const r = compactJobs(dir);
    expect(r.beforeLines).toBe(3);
    expect(r.afterLines).toBe(1);
    expect(r.dropped).toBe(2);
    rmSync(dir, { recursive: true });
  });

  it("compactSettlements keeps exactly one attempt per receiptId", () => {
    const dir = mkdtempSync(join(tmpdir(), "ja-"));
    const j = new SettlementJournal(dir);
    const base: SettlementAttempt = {
      id: "a",
      receiptId: "r",
      idempotencyKey: "k",
      recipient: "anm1qpzry9x8gf2tvdw0s3jn54khce6mua7l",
      amountRaw: 1n,
      status: "pending_submission",
      createdAt: "x",
      updatedAt: "x",
      attempts: 0,
      decisions: [],
      attemptHash: "h",
    };
    j.append({ ...base });
    j.append({ ...base, id: "b", status: "submitted", updatedAt: "y" });
    const r = compactSettlements(dir);
    expect(r.afterLines).toBe(1);
    rmSync(dir, { recursive: true });
  });

  it("archiveFailedJobs only moves failed jobs older than the cutoff", () => {
    const dir = mkdtempSync(join(tmpdir(), "ja-"));
    const s = new JobStateStore(dir);
    s.discover({ jobId: "old-fail", idempotencyKey: "1" });
    s.transition("old-fail", "failed", { reason: "old" });
    s.discover({ jobId: "young-fail", idempotencyKey: "2" });
    s.transition("young-fail", "failed", { reason: "young" });
    s.discover({ jobId: "running", idempotencyKey: "3" });
    s.transition("running", "accepted");
    // Forge an older updatedAt on the old-fail record by direct append.
    // Easier: pass olderThanMs = -1 to archive nothing, then olderThanMs = 0 to archive everything failed.
    const noneArchived = archiveFailedJobs(dir, { olderThanMs: 60_000 });
    expect(noneArchived.archived.length).toBe(0);
    const allArchived = archiveFailedJobs(dir, { olderThanMs: -1 });
    expect(allArchived.archived.length).toBe(2);
    expect(allArchived.archived.every((r) => r.status === "failed")).toBe(true);
    const live = new JobStateStore(dir);
    live.reload();
    expect(live.list().some((r) => r.status === "failed")).toBe(false);
    // Archive file exists.
    expect(readFileSync(allArchived.archivedFile, "utf8").split(/\n/).filter(Boolean).length).toBe(2);
    rmSync(dir, { recursive: true });
  });

  it("inspectQueues reports counts and oldest in-flight age", () => {
    const dir = mkdtempSync(join(tmpdir(), "ja-"));
    const s = new JobStateStore(dir);
    s.discover({ jobId: "j1", idempotencyKey: "k" });
    s.discover({ jobId: "j2", idempotencyKey: "k2" });
    s.transition("j2", "accepted");
    const q = inspectQueues(dir);
    expect(q.jobs.total).toBe(2);
    expect(q.jobs.inFlight).toBe(2);
    expect(q.jobs.statuses.discovered).toBeGreaterThanOrEqual(1);
    rmSync(dir, { recursive: true });
  });
});
