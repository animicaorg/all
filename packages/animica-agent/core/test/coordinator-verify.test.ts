import { mkdtempSync, rmSync, readFileSync, existsSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { describe, expect, it } from "vitest";

import {
  fetchCoordinatorSample,
  inspectOfflineQueue,
  readVerificationReports,
  verifyCoordinatorLive,
} from "../src/coordinator-verify.js";
import type { Job, Submission, VerificationOutcome } from "../src/useful-work.js";

function makeJob(id = "job-1"): Job {
  return {
    id,
    kind: "eval-bench",
    tier: "cpu-light",
    modelTarget: "animica-agent",
    modelVersion: "v0",
    dataManifest: "data:fixture",
    hyperparams: {},
    // On-wire rewardCapRaw must be a JSON-safe value. The hardened client
    // doesn't itself coerce; callers serialize bigints as strings.
    rewardCapRaw: "1" as unknown as bigint,
    publishedAt: new Date().toISOString(),
    expiresAt: new Date(Date.now() + 86_400_000).toISOString(),
    rules: "n/a",
  };
}

/**
 * Fake coordinator server. Returns shaped JSON for /health, /jobs, /jobs/:id,
 * and POST /jobs/:id/submissions. Optional knobs let tests inject failures.
 */
function makeFakeServer(opts: {
  health?: number; // status code
  jobs?: Job[] | "missing-array" | "auth-required";
  getJob?: "ok" | "missing-shape";
  submit?: "accepted" | "rejected" | "shape-error";
  authToken?: string; // simulate auth requirement
}): { fetchImpl: typeof fetch; calls: { url: string; auth?: string }[] } {
  const calls: { url: string; auth?: string }[] = [];
  const fetchImpl = (async (url: string | URL | Request, init?: RequestInit) => {
    const u = String(url);
    const auth = (init?.headers as Record<string, string> | undefined)?.authorization;
    calls.push({ url: u, auth });

    // Health endpoint.
    if (u.endsWith("/health")) {
      return new Response("", { status: opts.health ?? 200 });
    }
    // Auth check: /jobs etc. require auth if authToken is configured.
    if (opts.authToken && (!auth || !auth.includes(opts.authToken))) {
      return new Response("unauthorized", { status: 401 });
    }
    if (u.includes("/jobs/") && (init?.method ?? "GET") === "POST") {
      if (opts.submit === "shape-error") {
        return new Response("not json", { status: 200, headers: { "content-type": "application/json" } });
      }
      const outcome: VerificationOutcome = {
        submissionId: "sub-1",
        status: opts.submit === "rejected" ? "rejected" : "accepted",
        quality: 1,
        reason: "self-test",
        verifiers: ["fake"],
      };
      return new Response(JSON.stringify(outcome), {
        status: 200,
        headers: { "content-type": "application/json" },
      });
    }
    if (u.includes("/jobs/") && !u.endsWith("/jobs/")) {
      if (opts.getJob === "missing-shape") {
        return new Response(JSON.stringify({ wrong: "shape" }), {
          status: 200,
          headers: { "content-type": "application/json" },
        });
      }
      const id = u.split("/").pop() ?? "?";
      return new Response(JSON.stringify(makeJob(id)), {
        status: 200,
        headers: { "content-type": "application/json" },
      });
    }
    if (u.endsWith("/jobs")) {
      if (opts.jobs === "missing-array") {
        return new Response(JSON.stringify({ wrong: "shape" }), {
          status: 200,
          headers: { "content-type": "application/json" },
        });
      }
      const list = Array.isArray(opts.jobs) ? opts.jobs : [makeJob()];
      return new Response(JSON.stringify({ jobs: list }), {
        status: 200,
        headers: { "content-type": "application/json" },
      });
    }
    return new Response("not found", { status: 404 });
  }) as unknown as typeof fetch;
  return { fetchImpl, calls };
}

describe("verifyCoordinatorLive", () => {
  it("reports ok=true against a healthy fake server", async () => {
    const dir = mkdtempSync(join(tmpdir(), "cv-"));
    const { fetchImpl } = makeFakeServer({ health: 200 });
    const r = await verifyCoordinatorLive({
      baseUrl: "http://fake",
      stateDir: dir,
      fetchImpl,
      maxRetries: 0,
      sleep: async () => {},
    });
    expect(r.ok).toBe(true);
    expect(r.sample?.count).toBe(1);
    expect(r.checks.find((c) => c.name === "handshake.list-jobs")?.ok).toBe(true);
    expect(r.checks.find((c) => c.name === "handshake.get-job")?.ok).toBe(true);
    expect(r.checks.find((c) => c.name === "queue.enqueue")?.ok).toBe(true);
    expect(r.checks.find((c) => c.name === "queue.replay")?.ok).toBe(true);
    rmSync(dir, { recursive: true });
  });

  it("reports ok=false when /health is 500", async () => {
    const dir = mkdtempSync(join(tmpdir(), "cv-"));
    const { fetchImpl } = makeFakeServer({ health: 500 });
    const r = await verifyCoordinatorLive({
      baseUrl: "http://fake",
      stateDir: dir,
      fetchImpl,
      maxRetries: 0,
      sleep: async () => {},
    });
    expect(r.ok).toBe(false);
    expect(r.checks.find((c) => c.name === "doctor.health")?.ok).toBe(false);
    rmSync(dir, { recursive: true });
  });

  it("reports ok=false when /jobs is malformed", async () => {
    const dir = mkdtempSync(join(tmpdir(), "cv-"));
    const { fetchImpl } = makeFakeServer({ jobs: "missing-array" });
    const r = await verifyCoordinatorLive({
      baseUrl: "http://fake",
      stateDir: dir,
      fetchImpl,
      maxRetries: 0,
      sleep: async () => {},
    });
    expect(r.ok).toBe(false);
    expect(r.checks.find((c) => c.name === "doctor.jobs")?.ok).toBe(false);
    rmSync(dir, { recursive: true });
  });

  it("reports ok=false on auth failure (401)", async () => {
    const dir = mkdtempSync(join(tmpdir(), "cv-"));
    const { fetchImpl } = makeFakeServer({ authToken: "real-token" });
    const r = await verifyCoordinatorLive({
      baseUrl: "http://fake",
      stateDir: dir,
      fetchImpl,
      authEnv: "VERIFY_NONEXISTENT_TOKEN", // ensures no auth header set
      maxRetries: 0,
      sleep: async () => {},
    });
    expect(r.ok).toBe(false);
    expect(r.checks.find((c) => c.name === "handshake.list-jobs")?.ok).toBe(false);
    rmSync(dir, { recursive: true });
  });

  it("runs the optional fixture submission and records its outcome", async () => {
    const dir = mkdtempSync(join(tmpdir(), "cv-"));
    const { fetchImpl } = makeFakeServer({ submit: "accepted" });
    const r = await verifyCoordinatorLive({
      baseUrl: "http://fake",
      stateDir: dir,
      fetchImpl,
      submitFixture: true,
      maxRetries: 0,
      sleep: async () => {},
    });
    expect(r.fixtureSubmission?.status).toBe("accepted");
    rmSync(dir, { recursive: true });
  });

  it("persists a verification report to disk", async () => {
    const dir = mkdtempSync(join(tmpdir(), "cv-"));
    const { fetchImpl } = makeFakeServer({});
    const r = await verifyCoordinatorLive({
      baseUrl: "http://fake",
      stateDir: dir,
      fetchImpl,
      maxRetries: 0,
      sleep: async () => {},
    });
    expect(r.id).toBeTruthy();
    const file = join(dir, "coordinator-verifications.jsonl");
    expect(existsSync(file)).toBe(true);
    const persisted = readVerificationReports(dir);
    expect(persisted.length).toBe(1);
    expect(persisted[0].id).toBe(r.id);
    rmSync(dir, { recursive: true });
  });
});

describe("fetchCoordinatorSample", () => {
  it("returns up to N jobs against a healthy fake server", async () => {
    const { fetchImpl } = makeFakeServer({ jobs: [makeJob("a"), makeJob("b"), makeJob("c")] });
    const r = await fetchCoordinatorSample({
      baseUrl: "http://fake",
      fetchImpl,
      maxRetries: 0,
      sleep: async () => {},
    }, 2);
    expect(r.ok).toBe(true);
    expect(r.count).toBe(3);
    expect(r.jobs.length).toBe(2);
    expect(r.jobs.map((j) => j.id)).toEqual(["a", "b"]);
  });

  it("returns ok=false with an error on transport failure", async () => {
    const fetchImpl: typeof fetch = (async () => {
      throw new Error("ECONNREFUSED");
    }) as unknown as typeof fetch;
    const r = await fetchCoordinatorSample({
      baseUrl: "http://fake",
      fetchImpl,
      maxRetries: 0,
      sleep: async () => {},
    });
    expect(r.ok).toBe(false);
    expect(r.error).toMatch(/ECONNREFUSED/);
  });
});

describe("inspectOfflineQueue", () => {
  it("inspects without mutating the queue", () => {
    const dir = mkdtempSync(join(tmpdir(), "iq-"));
    // Empty queue
    const r1 = inspectOfflineQueue(dir);
    expect(r1.depth).toBe(0);
    rmSync(dir, { recursive: true });
  });
});
