/**
 * CLI surface for the coordinator verify-live workflow.
 *
 *   animica-agent coordinator verify-live --url <baseUrl> [--submit-fixture] [--json]
 *   animica-agent coordinator fetch-sample --url <baseUrl> [--limit N] [--json]
 *   animica-agent coordinator submit-fixture --url <baseUrl> --job-id <id> [--json]
 *   animica-agent coordinator queue [--json]
 *   animica-agent coordinator queue-replay --url <baseUrl> [--json]
 */

import { randomUUID } from "node:crypto";
import { mkdirSync } from "node:fs";
import { join } from "node:path";

import {
  checkCoordinatorFreshness,
  DEFAULT_FRESHNESS_WINDOW_MS,
  fetchCoordinatorSample,
  HardenedCoordinator,
  inspectOfflineQueue,
  latestVerificationReport,
  loadConfig,
  readVerificationReports,
  safeStringify,
  verifyCoordinatorLive,
  type Submission,
} from "@animica/agent-core";

import { boolFlag, stringFlag } from "../args.js";
import { c, fail, header, info, kv, table } from "../output.js";

export async function runCoordinatorVerifyLive(options: Record<string, string | boolean>): Promise<number> {
  const url = stringFlag(options, "url");
  if (!url) {
    fail("usage: coordinator verify-live --url <baseUrl> [--submit-fixture]");
    return 64;
  }
  const { config, paths } = loadConfig();
  const r = await verifyCoordinatorLive({
    baseUrl: url,
    stateDir: paths.stateDir,
    submitFixture: boolFlag(options, "submit-fixture", false),
    selfTestQueue: boolFlag(options, "self-test-queue", true),
    fixtureMinerAddress: config.minerAddress,
    maxRetries: Number.parseInt((stringFlag(options, "max-retries") ?? "0") as string, 10),
    worker: config.workerName,
  });
  if (boolFlag(options, "json", false)) {
    process.stdout.write(safeStringify(r, { indent: 2 }) + "\n");
    return r.ok ? 0 : 1;
  }
  header(`coordinator verify-live — ${r.ok ? c.green("OK") : c.red("FAIL")}`);
  kv([
    ["baseUrl", r.baseUrl],
    ["id", r.id],
    ["generatedAt", r.generatedAt],
  ]);
  header("Checks");
  for (const ch of r.checks) {
    const mark = ch.ok ? c.green("✓") : ch.level === "error" ? c.red("✗") : c.yellow("!");
    info(`  ${mark} ${ch.name}: ${ch.message}`);
  }
  if (r.sample) {
    header("Sample jobs");
    table(
      ["id", "kind", "tier"],
      r.sample.jobs.map((j) => [j.id, j.kind, j.tier]),
    );
    info(`  total: ${r.sample.count}`);
  }
  if (r.fixtureSubmission) {
    header("Fixture submission");
    kv([
      ["submissionId", r.fixtureSubmission.submissionId],
      ["status", r.fixtureSubmission.status],
      ["reason", r.fixtureSubmission.reason ?? "—"],
    ]);
  }
  info("");
  info(r.summary);
  return r.ok ? 0 : 1;
}

export async function runCoordinatorFetchSample(options: Record<string, string | boolean>): Promise<number> {
  const url = stringFlag(options, "url");
  if (!url) {
    fail("usage: coordinator fetch-sample --url <baseUrl> [--limit N]");
    return 64;
  }
  const limit = Number.parseInt((stringFlag(options, "limit") ?? "5") as string, 10);
  const r = await fetchCoordinatorSample({ baseUrl: url }, limit);
  if (boolFlag(options, "json", false)) {
    process.stdout.write(safeStringify(r, { indent: 2 }) + "\n");
    return r.ok ? 0 : 1;
  }
  header(`coordinator fetch-sample — ${r.ok ? c.green("OK") : c.red("FAIL")}`);
  kv([
    ["baseUrl", r.baseUrl],
    ["count", r.count],
    ["error", r.error ?? "—"],
  ]);
  table(
    ["id", "kind", "tier", "modelTarget"],
    r.jobs.map((j) => [j.id, j.kind, j.tier, j.modelTarget]),
  );
  return r.ok ? 0 : 1;
}

export async function runCoordinatorSubmitFixture(options: Record<string, string | boolean>): Promise<number> {
  const url = stringFlag(options, "url");
  const jobId = stringFlag(options, "job-id");
  if (!url || !jobId) {
    fail("usage: coordinator submit-fixture --url <baseUrl> --job-id <id>");
    return 64;
  }
  const { config, paths } = loadConfig();
  const queueDir = join(paths.stateDir, "coordinator-fixture-queue");
  mkdirSync(queueDir, { recursive: true });
  const coord = new HardenedCoordinator({
    baseUrl: url,
    queueDir,
    worker: config.workerName,
    maxRetries: 0,
  });
  const sub: Submission = {
    id: `fixture-${randomUUID()}`,
    jobId,
    minerAddress: config.minerAddress ?? "anm1fixture",
    artifactHash: "0".repeat(64),
    artifactPointer: "memory:fixture",
    metric: 0,
    elapsedMs: 0,
    submittedAt: new Date().toISOString(),
  };
  try {
    const outcome = await coord.submit(sub);
    if (boolFlag(options, "json", false)) {
      process.stdout.write(safeStringify(outcome, { indent: 2 }) + "\n");
    } else {
      header("fixture submission");
      kv([
        ["submissionId", outcome.submissionId],
        ["status", outcome.status],
        ["reason", outcome.reason ?? "—"],
        ["verifiers", outcome.verifiers.join(", ")],
      ]);
    }
    return outcome.status === "rejected" ? 1 : 0;
  } catch (err) {
    fail(`fixture submission failed: ${(err as Error).message}`);
    return 1;
  }
}

export function runCoordinatorQueue(options: Record<string, string | boolean>): number {
  const { paths } = loadConfig();
  const dir = stringFlag(options, "dir") ?? join(paths.stateDir, "coordinator-fixture-queue");
  const r = inspectOfflineQueue(dir);
  if (boolFlag(options, "json", false)) {
    process.stdout.write(safeStringify(r, { indent: 2 }) + "\n");
    return 0;
  }
  header(`offline queue (${dir})`);
  kv([["depth", r.depth]]);
  if (r.items.length === 0) {
    info(c.dim("  (empty)"));
  } else {
    table(["id", "jobId"], r.items.map((it) => [it.id, it.jobId]));
  }
  return 0;
}

export async function runCoordinatorQueueReplay(options: Record<string, string | boolean>): Promise<number> {
  const url = stringFlag(options, "url");
  if (!url) {
    fail("usage: coordinator queue-replay --url <baseUrl>");
    return 64;
  }
  const { config, paths } = loadConfig();
  const queueDir = stringFlag(options, "dir") ?? join(paths.stateDir, "coordinator-fixture-queue");
  const coord = new HardenedCoordinator({
    baseUrl: url,
    queueDir,
    worker: config.workerName,
    maxRetries: 0,
  });
  const results = await coord.replayQueue();
  if (boolFlag(options, "json", false)) {
    process.stdout.write(safeStringify(results, { indent: 2 }) + "\n");
  } else {
    header(`queue replay (${results.length})`);
    table(
      ["submissionId", "status / error"],
      results.map((r) => [
        r.submissionId,
        "error" in r.outcome ? `error: ${(r.outcome as { error: string }).error}` : (r.outcome as { status: string }).status,
      ]),
    );
  }
  return 0;
}

export function runCoordinatorLatest(options: Record<string, string | boolean>): number {
  const { paths } = loadConfig();
  const baseUrl = stringFlag(options, "url");
  const r = latestVerificationReport(paths.stateDir, baseUrl);
  if (boolFlag(options, "json", false)) {
    process.stdout.write(safeStringify(r ?? null, { indent: 2 }) + "\n");
    return r?.ok ? 0 : 1;
  }
  if (!r) {
    fail("no coordinator verification reports yet");
    return 1;
  }
  header(`Latest coordinator verification — ${r.ok ? c.green("OK") : c.red("FAIL")}`);
  kv([
    ["id", r.id],
    ["baseUrl", r.baseUrl],
    ["generatedAt", r.generatedAt],
    ["summary", r.summary],
  ]);
  return r.ok ? 0 : 1;
}

export function runCoordinatorFreshness(options: Record<string, string | boolean>): number {
  const { paths } = loadConfig();
  const baseUrl = stringFlag(options, "url");
  const window = Number.parseInt((stringFlag(options, "window-ms") ?? String(DEFAULT_FRESHNESS_WINDOW_MS)) as string, 10);
  const fresh = checkCoordinatorFreshness({ stateDir: paths.stateDir, windowMs: window, baseUrl });
  if (boolFlag(options, "json", false)) {
    process.stdout.write(safeStringify(fresh, { indent: 2 }) + "\n");
    return fresh.fresh ? 0 : 1;
  }
  header(`Coordinator freshness — ${fresh.fresh ? c.green("FRESH") : c.red("STALE")}`);
  kv([
    ["windowMs", fresh.windowMs],
    ["ageMs", fresh.ageMs ?? "—"],
    ["reason", fresh.reason ?? "—"],
    ["latestOkAt", fresh.latestOk?.generatedAt ?? "—"],
    ["latestAt", fresh.latest?.generatedAt ?? "—"],
  ]);
  return fresh.fresh ? 0 : 1;
}

export function runCoordinatorVerifyHistory(options: Record<string, string | boolean>): number {
  const { paths } = loadConfig();
  const limit = Number.parseInt((stringFlag(options, "limit") ?? "10") as string, 10);
  const reports = readVerificationReports(paths.stateDir, limit);
  if (boolFlag(options, "json", false)) {
    process.stdout.write(safeStringify(reports, { indent: 2 }) + "\n");
    return 0;
  }
  header(`coordinator verification history (${reports.length})`);
  table(
    ["at", "baseUrl", "ok", "errors"],
    reports.map((r) => [
      r.generatedAt,
      r.baseUrl,
      r.ok ? "yes" : "no",
      r.checks.filter((c) => !c.ok && c.level === "error").length,
    ]),
  );
  return 0;
}
