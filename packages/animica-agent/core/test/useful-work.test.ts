import { mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { describe, expect, it } from "vitest";

import { hashArtifact, LocalCoordinator, type Job, type Submission } from "../src/useful-work.js";

const job: Job = {
  id: "job-1",
  kind: "eval-bench",
  tier: "cpu-light",
  modelTarget: "animica-agent",
  modelVersion: "v0",
  dataManifest: "local://fixtures/eval",
  hyperparams: { seed: 42 },
  rewardCapRaw: 1_000_000_000_000_000_000n,
  publishedAt: new Date().toISOString(),
  expiresAt: new Date(Date.now() + 86400_000).toISOString(),
  rules: "score = 1 - mean(loss)",
};

describe("useful-work / local coordinator", () => {
  it("lists fixture jobs", async () => {
    const dir = mkdtempSync(join(tmpdir(), "uw-"));
    const c = new LocalCoordinator({ dataDir: dir, fixtureJobs: [job] });
    const list = await c.listJobs();
    expect(list.length).toBe(1);
    expect(list[0].id).toBe(job.id);
    rmSync(dir, { recursive: true });
  });

  it("rejects malformed artifact hashes", async () => {
    const dir = mkdtempSync(join(tmpdir(), "uw-"));
    const c = new LocalCoordinator({ dataDir: dir, fixtureJobs: [job] });
    const sub: Submission = {
      id: "s1",
      jobId: job.id,
      minerAddress: "anm1qpzry9x8gf2tvdw0s3jn54khce6mua7l",
      artifactHash: "not-a-sha256",
      artifactPointer: "x",
      metric: 0.5,
      elapsedMs: 10,
      submittedAt: new Date().toISOString(),
    };
    const o = await c.submit(sub);
    expect(o.status).toBe("rejected");
    rmSync(dir, { recursive: true });
  });

  it("accepts a well-formed submission and records a reward", async () => {
    const dir = mkdtempSync(join(tmpdir(), "uw-"));
    const c = new LocalCoordinator({ dataDir: dir, fixtureJobs: [job] });
    const buf = Buffer.from("artifact-bytes", "utf8");
    const sub: Submission = {
      id: "s2",
      jobId: job.id,
      minerAddress: "anm1qpzry9x8gf2tvdw0s3jn54khce6mua7l",
      artifactHash: hashArtifact(buf),
      artifactPointer: "memory",
      metric: 0.8,
      elapsedMs: 12,
      submittedAt: new Date().toISOString(),
    };
    const o = await c.submit(sub);
    expect(o.status).toBe("accepted");
    expect(o.quality).toBeGreaterThan(1);
    const rewards = await c.recentRewards(sub.minerAddress);
    expect(rewards.length).toBe(1);
    expect(rewards[0].rawAmount).toBeGreaterThan(0n);
    rmSync(dir, { recursive: true });
  });
});
