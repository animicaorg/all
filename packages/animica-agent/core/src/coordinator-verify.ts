/**
 * Coordinator verify-live workflow.
 *
 * The `HardenedCoordinator` is unit-tested with shimmed `fetch`. This module
 * lets an operator point the tool at a real or test AICF coordinator and
 * record a structured verification report to disk. The flow is:
 *
 *   1. Doctor check (auth presence, /health, /jobs schema).
 *   2. Authenticated handshake — confirms the bearer token round-trips.
 *   3. Sample fetch — pulls a real or fixture job and validates its shape.
 *   4. (Optional) fixture submission — emits a no-op submission that the
 *      coordinator can accept or reject; we never claim "accepted" without
 *      a fully-shaped VerificationOutcome reply.
 *   5. Offline-queue self-test — verifies that a transient error queues a
 *      submission and that `replayQueue` clears it once the upstream recovers.
 *
 * Every report is persisted to <stateDir>/coordinator-verifications.jsonl
 * so an operator audit log records exactly what was tested and when.
 *
 * Fails closed: anything that does not look like the expected contract
 * (schema mismatch, malformed JSON, auth refused) is recorded as an error
 * and the overall `ok` flag is set to false.
 */

import { appendFileSync, existsSync, mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { randomUUID } from "node:crypto";
import { join } from "node:path";

import {
  coordinatorDoctor,
  CoordinatorAuthError,
  CoordinatorPermanentError,
  CoordinatorShapeError,
  CoordinatorTransientError,
  HardenedCoordinator,
  OfflineSubmissionQueue,
  type HardenedCoordinatorOptions,
} from "./coordinator-hardened.js";
import { safeParse, safeStringify } from "./safe-json.js";
import type { Job, Submission, VerificationOutcome } from "./useful-work.js";

export interface CoordinatorVerifyOptions extends HardenedCoordinatorOptions {
  /** State dir where verification reports are journaled. */
  stateDir: string;
  /** Optional miner address used by the fixture submission probe. */
  fixtureMinerAddress?: string;
  /** When true, runs the optional fixture submission. Default false. */
  submitFixture?: boolean;
  /** When true, runs the offline-queue self-test. Default true. */
  selfTestQueue?: boolean;
}

export interface CoordinatorVerifyCheck {
  name: string;
  ok: boolean;
  level: "info" | "warning" | "error";
  message: string;
  detail?: Record<string, unknown>;
}

export interface CoordinatorVerifyReport {
  id: string;
  baseUrl: string;
  generatedAt: string;
  ok: boolean;
  checks: CoordinatorVerifyCheck[];
  sample?: {
    jobs: { id: string; kind: string; tier: string }[];
    count: number;
  };
  fixtureSubmission?: {
    submissionId: string;
    status: "accepted" | "rejected" | "pending" | "challenged" | "error";
    reason?: string;
  };
  summary: string;
}

/** Records a verification report to disk and returns it. */
function persist(stateDir: string, report: CoordinatorVerifyReport): CoordinatorVerifyReport {
  mkdirSync(stateDir, { recursive: true });
  const file = join(stateDir, "coordinator-verifications.jsonl");
  appendFileSync(file, safeStringify(report) + "\n", "utf8");
  return report;
}

export function readVerificationReports(stateDir: string, limit = 25): CoordinatorVerifyReport[] {
  const file = join(stateDir, "coordinator-verifications.jsonl");
  if (!existsSync(file)) return [];
  const lines = readFileSync(file, "utf8").split(/\r?\n/).filter(Boolean).slice(-limit);
  const out: CoordinatorVerifyReport[] = [];
  for (const l of lines) {
    try {
      out.push(safeParse<CoordinatorVerifyReport>(l));
    } catch {
      /* skip corrupt line */
    }
  }
  return out;
}

/**
 * Runs the full verification workflow and persists a structured report.
 */
export async function verifyCoordinatorLive(opts: CoordinatorVerifyOptions): Promise<CoordinatorVerifyReport> {
  const checks: CoordinatorVerifyCheck[] = [];
  const baseUrl = opts.baseUrl;

  // 1. Doctor check.
  let doctor: Awaited<ReturnType<typeof coordinatorDoctor>> | null = null;
  try {
    doctor = await coordinatorDoctor(opts);
    checks.push({
      name: "doctor.health",
      ok: doctor.healthEndpoint.ok,
      level: doctor.healthEndpoint.ok ? "info" : "error",
      message: doctor.healthEndpoint.ok
        ? `health endpoint returned ${doctor.healthEndpoint.status}`
        : `health endpoint unhealthy: ${doctor.healthEndpoint.error ?? doctor.healthEndpoint.status}`,
    });
    checks.push({
      name: "doctor.jobs",
      ok: doctor.jobsEndpoint.ok,
      level: doctor.jobsEndpoint.ok ? "info" : "error",
      message: doctor.jobsEndpoint.ok
        ? `/jobs returned ${doctor.jobsEndpoint.sampleCount} entries`
        : `/jobs unhealthy: ${doctor.jobsEndpoint.error}`,
    });
    checks.push({
      name: "doctor.auth",
      ok: doctor.authConfigured,
      level: doctor.authConfigured ? "info" : "warning",
      message: doctor.authConfigured ? "auth token present in env" : "no auth token in env; calls will be unauthenticated",
    });
  } catch (err) {
    checks.push({
      name: "doctor",
      ok: false,
      level: "error",
      message: `doctor failed: ${(err as Error).message.slice(0, 200)}`,
    });
  }

  // 2. Authenticated handshake + sample fetch via HardenedCoordinator.
  const client = new HardenedCoordinator({ ...opts, name: opts.name ?? "verify-live" });
  let sample: CoordinatorVerifyReport["sample"];
  let listOk = false;
  try {
    const jobs = await client.listJobs();
    listOk = true;
    checks.push({
      name: "handshake.list-jobs",
      ok: true,
      level: "info",
      message: `authenticated listJobs returned ${jobs.length} jobs`,
    });
    sample = {
      jobs: jobs.slice(0, 5).map((j: Job) => ({ id: j.id, kind: j.kind, tier: j.tier })),
      count: jobs.length,
    };
    // 3. Sample fetch — pull one job by id and round-trip schema.
    if (jobs.length > 0) {
      try {
        const one = await client.getJob(jobs[0].id);
        const okShape = !!one && typeof one.id === "string" && one.id === jobs[0].id;
        checks.push({
          name: "handshake.get-job",
          ok: okShape,
          level: okShape ? "info" : "error",
          message: okShape ? `getJob(${jobs[0].id}) returned a well-shaped Job` : `getJob round-trip failed`,
        });
      } catch (err) {
        checks.push({
          name: "handshake.get-job",
          ok: false,
          level: "error",
          message: `getJob error: ${(err as Error).message.slice(0, 200)}`,
        });
      }
    }
  } catch (err) {
    listOk = false;
    const cls = classifyCoordError(err);
    checks.push({
      name: "handshake.list-jobs",
      ok: false,
      level: cls === "auth" ? "error" : "error",
      message: `listJobs (${cls}) failed: ${(err as Error).message.slice(0, 200)}`,
    });
  }

  // 4. Optional fixture submission.
  let fixtureSubmission: CoordinatorVerifyReport["fixtureSubmission"];
  if (opts.submitFixture && listOk && sample && sample.count > 0) {
    const sub: Submission = {
      id: `verify-fixture-${randomUUID()}`,
      jobId: sample.jobs[0].id,
      minerAddress: opts.fixtureMinerAddress ?? "anm1verify-fixture",
      artifactHash: "0".repeat(64),
      artifactPointer: "memory:fixture",
      metric: 0,
      elapsedMs: 0,
      submittedAt: new Date().toISOString(),
    };
    try {
      const outcome: VerificationOutcome = await client.submit(sub);
      fixtureSubmission = {
        submissionId: outcome.submissionId,
        status: outcome.status,
        reason: outcome.reason,
      };
      checks.push({
        name: "fixture.submit",
        ok: outcome.status !== "rejected" || true, // both accept + reject are well-formed answers
        level: "info",
        message: `fixture submission returned status=${outcome.status}`,
      });
    } catch (err) {
      fixtureSubmission = {
        submissionId: sub.id,
        status: "error",
        reason: (err as Error).message?.slice(0, 200),
      };
      checks.push({
        name: "fixture.submit",
        ok: false,
        level: "error",
        message: `fixture submission errored: ${(err as Error).message.slice(0, 200)}`,
      });
    }
  }

  // 5. Offline queue self-test. We run this with a *transient-only* fake
  //    fetch so we don't perturb the real coordinator. It proves the queue
  //    + replay machinery works in this process against this config.
  if (opts.selfTestQueue !== false) {
    const tempQueueDir = join(opts.stateDir, "coordinator-verify-queue");
    mkdirSync(tempQueueDir, { recursive: true });
    // Reset the queue every time we self-test.
    try {
      writeFileSync(join(tempQueueDir, "submission-queue.jsonl"), "", "utf8");
    } catch {
      /* fresh dir is fine */
    }
    let calls = 0;
    const fakeFetch = (async () => {
      calls++;
      if (calls === 1) {
        return new Response("busy", { status: 503 });
      }
      return new Response(
        JSON.stringify({ submissionId: "verify-sub", status: "accepted", quality: 1, verifiers: ["self-test"] }),
        { status: 200, headers: { "content-type": "application/json" } },
      );
    }) as unknown as typeof fetch;
    const selfTest = new HardenedCoordinator({
      baseUrl: opts.baseUrl,
      authEnv: opts.authEnv,
      maxRetries: 0,
      fetchImpl: fakeFetch,
      sleep: async () => {},
      queueDir: tempQueueDir,
    });
    const sub: Submission = {
      id: `selftest-${randomUUID()}`,
      jobId: "selftest-job",
      minerAddress: "anm1selftest",
      artifactHash: "0".repeat(64),
      artifactPointer: "memory:selftest",
      metric: 0,
      elapsedMs: 0,
      submittedAt: new Date().toISOString(),
    };
    const first = await selfTest.submit(sub);
    const queuedOk = first.status === "pending" && selfTest.queueDepth() === 1;
    checks.push({
      name: "queue.enqueue",
      ok: queuedOk,
      level: queuedOk ? "info" : "error",
      message: queuedOk
        ? "transient failure enqueued submission as expected"
        : `transient failure did not enqueue (status=${first.status})`,
    });
    const replayed = await selfTest.replayQueue();
    const replayOk = replayed.length === 1 && "outcome" in replayed[0] && (replayed[0].outcome as { status?: string }).status === "accepted";
    checks.push({
      name: "queue.replay",
      ok: replayOk,
      level: replayOk ? "info" : "error",
      message: replayOk ? "replayQueue cleared the queue on upstream recovery" : "replayQueue did not clear the queue",
    });
    // Tidy up the self-test queue so future verifications start clean.
    try {
      const q = new OfflineSubmissionQueue(tempQueueDir);
      for (const item of q.list()) q.remove(item.id);
    } catch {
      /* harmless */
    }
  }

  const ok = checks.every((c) => c.ok || c.level !== "error");
  const errors = checks.filter((c) => !c.ok && c.level === "error").length;
  const report: CoordinatorVerifyReport = {
    id: randomUUID(),
    baseUrl,
    generatedAt: new Date().toISOString(),
    ok,
    checks,
    sample,
    fixtureSubmission,
    summary: ok
      ? `verify-live: ok — coordinator ${baseUrl} matches the expected contract`
      : `verify-live: NO-GO — ${errors} blocker(s) on ${baseUrl}`,
  };
  return persist(opts.stateDir, report);
}

function classifyCoordError(err: unknown): "auth" | "permanent" | "shape" | "transient" | "unknown" {
  if (err instanceof CoordinatorAuthError) return "auth";
  if (err instanceof CoordinatorPermanentError) return "permanent";
  if (err instanceof CoordinatorShapeError) return "shape";
  if (err instanceof CoordinatorTransientError) return "transient";
  return "unknown";
}

/**
 * Coordinator fetch-sample — light-touch read-only fetch with a structured
 * result. Suitable for ops dashboards and routine smoke checks.
 */
export interface CoordinatorFetchSampleResult {
  ok: boolean;
  baseUrl: string;
  count: number;
  jobs: { id: string; kind: string; tier: string; modelTarget: string }[];
  error?: string;
}

export async function fetchCoordinatorSample(
  opts: HardenedCoordinatorOptions,
  limit = 5,
): Promise<CoordinatorFetchSampleResult> {
  const client = new HardenedCoordinator({ ...opts, name: opts.name ?? "fetch-sample" });
  try {
    const jobs = await client.listJobs();
    return {
      ok: true,
      baseUrl: opts.baseUrl,
      count: jobs.length,
      jobs: jobs.slice(0, limit).map((j: Job) => ({
        id: j.id,
        kind: j.kind,
        tier: j.tier,
        modelTarget: j.modelTarget,
      })),
    };
  } catch (err) {
    return {
      ok: false,
      baseUrl: opts.baseUrl,
      count: 0,
      jobs: [],
      error: (err as Error).message?.slice(0, 200),
    };
  }
}

/** Inspect the offline queue without modifying it. */
export function inspectOfflineQueue(queueDir: string): { depth: number; items: { id: string; jobId: string }[] } {
  const q = new OfflineSubmissionQueue(queueDir);
  const items = q.list().map((it) => ({ id: it.submission.id, jobId: it.submission.jobId }));
  return { depth: items.length, items };
}

/** Default freshness window: a verify-live report older than this is stale. */
export const DEFAULT_FRESHNESS_WINDOW_MS = 24 * 60 * 60_000;

export interface CoordinatorFreshness {
  /** True if the most recent ok=true report is within the freshness window. */
  fresh: boolean;
  /** Most recent report (any verdict), if any. */
  latest?: CoordinatorVerifyReport;
  /** Most recent ok=true report, if any. */
  latestOk?: CoordinatorVerifyReport;
  /** Age in ms of `latestOk`, if any. */
  ageMs?: number;
  /** Freshness window used. */
  windowMs: number;
  /** Optional baseUrl filter. */
  baseUrl?: string;
  /** Reason for non-freshness, for operator display. */
  reason?:
    | "no-report"
    | "latest-not-ok"
    | "latest-stale"
    | "wrong-baseurl";
}

export interface FreshnessOptions {
  stateDir: string;
  /** Window in ms. Defaults to DEFAULT_FRESHNESS_WINDOW_MS. */
  windowMs?: number;
  /** Restrict to a specific baseUrl. */
  baseUrl?: string;
  /** Clock override for tests. */
  now?: () => number;
}

/**
 * Reads the persisted verification log and reports whether a recent
 * successful verification exists.
 */
export function checkCoordinatorFreshness(opts: FreshnessOptions): CoordinatorFreshness {
  const windowMs = opts.windowMs ?? DEFAULT_FRESHNESS_WINDOW_MS;
  const now = opts.now ? opts.now() : Date.now();
  const all = readVerificationReports(opts.stateDir, 1000);
  const filtered = opts.baseUrl ? all.filter((r) => r.baseUrl === opts.baseUrl) : all;
  if (filtered.length === 0) {
    return { fresh: false, windowMs, baseUrl: opts.baseUrl, reason: "no-report" };
  }
  const latest = filtered[filtered.length - 1];
  const latestOk = [...filtered].reverse().find((r) => r.ok);
  // A later failed report invalidates an earlier ok one. This is intentional:
  // a regression should block live submits until the operator re-verifies.
  if (!latest.ok) {
    return { fresh: false, latest, latestOk, windowMs, baseUrl: opts.baseUrl, reason: "latest-not-ok" };
  }
  const ageMs = now - new Date(latest.generatedAt).getTime();
  if (ageMs > windowMs) {
    return { fresh: false, latest, latestOk, ageMs, windowMs, baseUrl: opts.baseUrl, reason: "latest-stale" };
  }
  return { fresh: true, latest, latestOk, ageMs, windowMs, baseUrl: opts.baseUrl };
}

/** Convenience: returns the most-recent report (no freshness window). */
export function latestVerificationReport(stateDir: string, baseUrl?: string): CoordinatorVerifyReport | null {
  const all = readVerificationReports(stateDir, 1000);
  const filtered = baseUrl ? all.filter((r) => r.baseUrl === baseUrl) : all;
  return filtered.length > 0 ? filtered[filtered.length - 1] : null;
}
