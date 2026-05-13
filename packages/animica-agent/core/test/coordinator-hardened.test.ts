import { mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { describe, expect, it } from "vitest";

import {
  CoordinatorAuthError,
  CoordinatorPermanentError,
  CoordinatorShapeError,
  CoordinatorTransientError,
  HardenedCoordinator,
  OfflineSubmissionQueue,
} from "../src/coordinator-hardened.js";
import type { Job, Submission, VerificationOutcome } from "../src/useful-work.js";

function fakeResponse(status: number, body: unknown): Response {
  return new Response(typeof body === "string" ? body : JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json" },
  });
}

function makeJob(overrides: Partial<Job> = {}): Job {
  return {
    id: "jx",
    kind: "eval-bench",
    tier: "cpu-light",
    modelTarget: "animica-agent",
    modelVersion: "v0",
    dataManifest: "data:noop",
    hyperparams: {},
    rewardCapRaw: 1n,
    publishedAt: new Date().toISOString(),
    expiresAt: new Date(Date.now() + 86400_000).toISOString(),
    rules: "n/a",
    ...overrides,
  };
}

function makeSubmission(overrides: Partial<Submission> = {}): Submission {
  return {
    id: "sub-1",
    jobId: "jx",
    minerAddress: "anm1qpzry9x8gf2tvdw0s3jn54khce6mua7l",
    artifactHash: "x".repeat(64),
    artifactPointer: "memory",
    metric: 0.5,
    elapsedMs: 1,
    submittedAt: new Date().toISOString(),
    ...overrides,
  };
}

describe("HardenedCoordinator request classification", () => {
  it("retries 5xx as transient and eventually surfaces CoordinatorTransientError", async () => {
    let calls = 0;
    const fetchImpl: typeof fetch = async () => {
      calls++;
      return fakeResponse(503, { error: "busy" });
    };
    const c = new HardenedCoordinator({
      baseUrl: "http://x",
      maxRetries: 2,
      backoffMs: 1,
      backoffCapMs: 1,
      fetchImpl,
      sleep: async () => {},
    });
    await expect(c.listJobs()).rejects.toBeInstanceOf(CoordinatorTransientError);
    expect(calls).toBe(3); // initial + 2 retries
  });

  it("does NOT retry 4xx; surfaces CoordinatorPermanentError immediately", async () => {
    let calls = 0;
    const fetchImpl: typeof fetch = async () => {
      calls++;
      return fakeResponse(400, "bad request");
    };
    const c = new HardenedCoordinator({
      baseUrl: "http://x",
      maxRetries: 5,
      backoffMs: 1,
      fetchImpl,
      sleep: async () => {},
    });
    await expect(c.listJobs()).rejects.toBeInstanceOf(CoordinatorPermanentError);
    expect(calls).toBe(1);
  });

  it("surfaces 401/403 as CoordinatorAuthError without retry", async () => {
    let calls = 0;
    const fetchImpl: typeof fetch = async () => {
      calls++;
      return fakeResponse(401, "unauthorized");
    };
    const c = new HardenedCoordinator({
      baseUrl: "http://x",
      maxRetries: 5,
      backoffMs: 1,
      fetchImpl,
      sleep: async () => {},
    });
    await expect(c.listJobs()).rejects.toBeInstanceOf(CoordinatorAuthError);
    expect(calls).toBe(1);
  });

  it("rejects malformed JSON as CoordinatorShapeError", async () => {
    const fetchImpl: typeof fetch = async () => fakeResponse(200, "<<not json>>");
    const c = new HardenedCoordinator({ baseUrl: "http://x", maxRetries: 0, fetchImpl, sleep: async () => {} });
    await expect(c.listJobs()).rejects.toBeInstanceOf(CoordinatorShapeError);
  });

  it("rejects malformed Job shape", async () => {
    const fetchImpl: typeof fetch = async () => fakeResponse(200, { jobs: [{ id: "x" }] });
    const c = new HardenedCoordinator({ baseUrl: "http://x", maxRetries: 0, fetchImpl, sleep: async () => {} });
    await expect(c.listJobs()).rejects.toBeInstanceOf(CoordinatorShapeError);
  });

  it("returns null on /jobs/:id 404 via permanent-error mapping", async () => {
    const fetchImpl: typeof fetch = async (_url, _init) => fakeResponse(404, "not found");
    const c = new HardenedCoordinator({ baseUrl: "http://x", maxRetries: 0, fetchImpl, sleep: async () => {} });
    const r = await c.getJob("missing");
    expect(r).toBeNull();
  });

  it("submits successfully against a well-shaped response", async () => {
    const fetchImpl: typeof fetch = async () =>
      fakeResponse(200, { submissionId: "sub-1", status: "accepted", quality: 1.1, verifiers: ["t"] } as VerificationOutcome);
    const c = new HardenedCoordinator({ baseUrl: "http://x", maxRetries: 0, fetchImpl, sleep: async () => {} });
    const r = await c.submit(makeSubmission());
    expect(r.status).toBe("accepted");
  });
});

describe("HardenedCoordinator offline queue", () => {
  it("queues a submission when transient and returns pending outcome", async () => {
    const dir = mkdtempSync(join(tmpdir(), "hc-"));
    const fetchImpl: typeof fetch = async () => fakeResponse(503, { error: "busy" });
    const c = new HardenedCoordinator({
      baseUrl: "http://x",
      maxRetries: 0,
      fetchImpl,
      sleep: async () => {},
      queueDir: dir,
    });
    const r = await c.submit(makeSubmission());
    expect(r.status).toBe("pending");
    expect(c.queueDepth()).toBe(1);
    rmSync(dir, { recursive: true });
  });

  it("replays the queue successfully when upstream recovers", async () => {
    const dir = mkdtempSync(join(tmpdir(), "hc-"));
    // First call (in the submit) → 503; second call (during replay) → 200.
    let calls = 0;
    const fetchImpl: typeof fetch = async () => {
      calls++;
      if (calls === 1) return fakeResponse(503, "busy");
      return fakeResponse(200, { submissionId: "sub-1", status: "accepted", quality: 1, verifiers: ["t"] });
    };
    const c = new HardenedCoordinator({
      baseUrl: "http://x",
      maxRetries: 0,
      fetchImpl,
      sleep: async () => {},
      queueDir: dir,
    });
    await c.submit(makeSubmission());
    expect(c.queueDepth()).toBe(1);
    const out = await c.replayQueue();
    expect(out[0].outcome).toMatchObject({ status: "accepted" });
    expect(c.queueDepth()).toBe(0);
    rmSync(dir, { recursive: true });
  });
});

describe("OfflineSubmissionQueue", () => {
  it("enqueue / list / remove round-trips", () => {
    const dir = mkdtempSync(join(tmpdir(), "qq-"));
    const q = new OfflineSubmissionQueue(dir);
    q.enqueue(makeSubmission({ id: "a" }));
    q.enqueue(makeSubmission({ id: "b" }));
    expect(q.size()).toBe(2);
    q.remove("a");
    expect(q.size()).toBe(1);
    expect(q.list()[0].submission.id).toBe("b");
    rmSync(dir, { recursive: true });
  });
});
