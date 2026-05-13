import { mkdtempSync, readFileSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { describe, expect, it } from "vitest";

import { DEFAULT_CONFIG } from "../src/config.js";
import { JobRunner } from "../src/job-runner.js";
import { hashArtifact, type Job } from "../src/useful-work.js";

function makeJob(overrides: Partial<Job> = {}): Job {
  return {
    id: "job-test",
    kind: "eval-bench",
    tier: "cpu-light",
    modelTarget: "animica-agent",
    modelVersion: "v0",
    dataManifest:
      'data:{"prompt":"add","expected":"the sum is two","predicted":"the sum is two"}\n{"prompt":"sub","expected":"three","predicted":"three"}',
    hyperparams: {},
    rewardCapRaw: 0n,
    publishedAt: new Date().toISOString(),
    expiresAt: new Date(Date.now() + 86400_000).toISOString(),
    rules: "jaccard token overlap",
    ...overrides,
  };
}

describe("JobRunner", () => {
  it("runs eval-bench deterministically and produces a real artifact", async () => {
    const dir = mkdtempSync(join(tmpdir(), "jobrun-"));
    const cfg = { ...DEFAULT_CONFIG, workspacePath: dir, minerAddress: "anm1qpzry9x8gf2tvdw0s3jn54khce6mua7l" };
    const r = new JobRunner(cfg, dir);
    const out = await r.run(makeJob());
    expect(out.kind).toBe("executed");
    expect(out.submission.metric).toBeGreaterThan(0.9);
    expect(out.submission.metric).toBeLessThanOrEqual(1);
    expect(out.submission.artifactHash).toMatch(/^[0-9a-f]{64}$/);
    const body = readFileSync(out.artifactPath, "utf8");
    expect(hashArtifact(Buffer.from(body, "utf8"))).toBe(out.submission.artifactHash);
    rmSync(dir, { recursive: true });
  });

  it("embedding kind produces a 256-dim checksum", async () => {
    const dir = mkdtempSync(join(tmpdir(), "jobrun-"));
    const cfg = { ...DEFAULT_CONFIG, workspacePath: dir, minerAddress: "anm1qpzry9x8gf2tvdw0s3jn54khce6mua7l" };
    const r = new JobRunner(cfg, dir);
    const out = await r.run(makeJob({ kind: "embedding", dataManifest: 'data:"alpha"\n"beta"\n"gamma"' }));
    expect(out.kind).toBe("executed");
    expect(out.submission.metric).toBeGreaterThan(0);
    const body = JSON.parse(readFileSync(out.artifactPath, "utf8")) as { artifact: { dims: number; checksum: string } };
    expect(body.artifact.dims).toBe(256);
    expect(body.artifact.checksum).toMatch(/^[0-9a-f]{64}$/);
    rmSync(dir, { recursive: true });
  });

  it("dedupe kind reports unique-fraction", async () => {
    const dir = mkdtempSync(join(tmpdir(), "jobrun-"));
    const cfg = { ...DEFAULT_CONFIG, workspacePath: dir, minerAddress: "anm1qpzry9x8gf2tvdw0s3jn54khce6mua7l" };
    const r = new JobRunner(cfg, dir);
    const out = await r.run(makeJob({ kind: "dedupe", dataManifest: 'data:"a"\n"a"\n"b"\n"c"' }));
    expect(out.kind).toBe("executed");
    // 3 unique of 4 total
    expect(out.submission.metric).toBeCloseTo(0.75, 5);
    rmSync(dir, { recursive: true });
  });

  it("refuses an unsupported job kind rather than fabricating a metric", async () => {
    const dir = mkdtempSync(join(tmpdir(), "jobrun-"));
    const cfg = { ...DEFAULT_CONFIG, workspacePath: dir, minerAddress: "anm1qpzry9x8gf2tvdw0s3jn54khce6mua7l" };
    const r = new JobRunner(cfg, dir);
    const out = await r.run(makeJob({ kind: "lora-finetune" as Job["kind"] }));
    expect(out.kind).toBe("unsupported");
    expect(out.reason).toMatch(/not implemented in the local runner/);
    rmSync(dir, { recursive: true });
  });
});
