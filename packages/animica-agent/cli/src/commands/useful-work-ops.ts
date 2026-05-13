/**
 * CLI surface for the hardened useful-work subsystem.
 *
 * Commands:
 *   animica-agent settlement list
 *   animica-agent settlement show <receiptId>
 *   animica-agent settlement resume [<receiptId>...]
 *   animica-agent journal compact [--settlements] [--jobs]
 *   animica-agent journal archive --older-than <ms> [--reason <substring>]
 *   animica-agent journal inspect
 *   animica-agent metrics [--json]
 *   animica-agent doctor useful-work [--coordinator-url <url>] [--json]
 *   animica-agent hybrid plan [--json]
 *   animica-agent coordinator doctor --url <url> [--json]
 *   animica-agent payout audit [--limit N]
 */

import {
  archiveFailedJobs,
  compactJobs,
  compactSettlements,
  coordinatorDoctor,
  doctorUsefulWork,
  inspectQueues,
  loadConfig,
  MetricsRegistry,
  NodeWalletSigner,
  PayoutAuditor,
  planFromConfig,
  RpcConfirmationPoller,
  safeStringify,
  SettlementEngine,
  SettlementJournal,
} from "@animica/agent-core";

import { boolFlag, stringFlag } from "../args.js";
import { c, fail, header, info, kv, ok, table } from "../output.js";

export async function runSettlementList(options: Record<string, string | boolean>): Promise<number> {
  const { paths } = loadConfig();
  const journal = new SettlementJournal(paths.stateDir);
  journal.reload();
  const list = journal.list();
  if (boolFlag(options, "json", false)) {
    process.stdout.write(safeStringify(list, { indent: 2 }) + "\n");
    return 0;
  }
  header(`Settlement attempts (${list.length})`);
  table(
    ["receiptId", "status", "amountANM", "txHash", "attempts", "updatedAt"],
    list.map((a) => [
      a.receiptId.slice(0, 12),
      a.status,
      a.amountRaw.toString(),
      a.txHash?.slice(0, 16) ?? "—",
      a.attempts,
      a.updatedAt,
    ]),
  );
  return 0;
}

export async function runSettlementShow(positionals: string[]): Promise<number> {
  const id = positionals[0];
  if (!id) {
    fail("usage: animica-agent settlement show <receiptId>");
    return 64;
  }
  const { paths } = loadConfig();
  const journal = new SettlementJournal(paths.stateDir);
  journal.reload();
  const all = journal.all(id);
  if (all.length === 0) {
    fail(`no settlement records for receiptId=${id}`);
    return 1;
  }
  process.stdout.write(safeStringify(all, { indent: 2 }) + "\n");
  return 0;
}

export async function runSettlementResume(positionals: string[], options: Record<string, string | boolean>): Promise<number> {
  const { config, paths } = loadConfig();
  const journal = new SettlementJournal(paths.stateDir);
  journal.reload();
  const allList = journal.list();
  const targets =
    positionals.length > 0
      ? allList.filter((a) => positionals.includes(a.receiptId))
      : allList.filter(
          (a) =>
            a.status === "pending_submission" ||
            a.status === "submitted" ||
            a.status === "confirming" ||
            a.status === "failed_transient",
        );
  if (targets.length === 0) {
    info("nothing to resume.");
    return 0;
  }
  const signer = new NodeWalletSigner();
  const engine = new SettlementEngine({
    signer,
    journal,
    confirmationDepth: Number.parseInt(stringFlag(options, "depth", "1") as string, 10),
    maxAttempts: 5,
    attemptDeadlineMs: 24 * 60 * 60 * 1000,
    confirmIntervalMs: 2000,
    confirmMaxPolls: 30,
  });
  engine.attachPoller(new RpcConfirmationPoller(config.rpcUrl));
  const results: { receiptId: string; status: string }[] = [];
  for (const t of targets) {
    try {
      const driven = await engine.drive(t.receiptId);
      results.push({ receiptId: t.receiptId, status: driven.status });
    } catch (err) {
      results.push({ receiptId: t.receiptId, status: `error: ${(err as Error).message.slice(0, 80)}` });
    }
  }
  if (boolFlag(options, "json", false)) {
    process.stdout.write(safeStringify(results, { indent: 2 }) + "\n");
  } else {
    header(`Settlement resume (${results.length})`);
    table(["receiptId", "status"], results.map((r) => [r.receiptId.slice(0, 16), r.status]));
  }
  return 0;
}

export function runJournalCompact(options: Record<string, string | boolean>): number {
  const { paths } = loadConfig();
  const doSettlements = boolFlag(options, "settlements", true);
  const doJobs = boolFlag(options, "jobs", true);
  const out: unknown[] = [];
  if (doJobs) out.push({ subject: "jobs", ...compactJobs(paths.stateDir) });
  if (doSettlements) out.push({ subject: "settlements", ...compactSettlements(paths.stateDir) });
  if (boolFlag(options, "json", false)) {
    process.stdout.write(safeStringify(out, { indent: 2 }) + "\n");
  } else {
    for (const r of out as { subject: string; file: string; beforeLines: number; afterLines: number; dropped: number }[]) {
      kv([
        ["subject", r.subject],
        ["file", r.file],
        ["before", r.beforeLines],
        ["after", r.afterLines],
        ["dropped", r.dropped],
      ]);
    }
  }
  ok("compaction complete");
  return 0;
}

export function runJournalArchive(options: Record<string, string | boolean>): number {
  const { paths } = loadConfig();
  const olderThanMs = Number.parseInt(stringFlag(options, "older-than", "604800000") as string, 10);
  if (!Number.isFinite(olderThanMs)) {
    fail("--older-than must be a finite integer (ms)");
    return 64;
  }
  const reasonsLike = stringFlag(options, "reason");
  const r = archiveFailedJobs(paths.stateDir, { olderThanMs, reasonsLike });
  if (boolFlag(options, "json", false)) {
    process.stdout.write(safeStringify(r, { indent: 2 }) + "\n");
  } else {
    kv([
      ["archived", r.archived.length],
      ["archiveFile", r.archivedFile],
      ["remaining", r.remaining],
    ]);
    ok("archive complete");
  }
  return 0;
}

export function runJournalInspect(options: Record<string, string | boolean>): number {
  const { paths } = loadConfig();
  const q = inspectQueues(paths.stateDir);
  if (boolFlag(options, "json", false)) {
    process.stdout.write(safeStringify(q, { indent: 2 }) + "\n");
    return 0;
  }
  header("Jobs");
  kv([
    ["total", q.jobs.total],
    ["inFlight", q.jobs.inFlight],
    ["terminal", q.jobs.terminal],
    ["oldestInFlightAt", q.jobs.oldestInFlightAt ?? "—"],
    ["journalSizeBytes", q.jobs.journalSizeBytes],
  ]);
  header("Settlements");
  kv([
    ["total", q.settlements.total],
    ["inFlight", q.settlements.inFlight],
    ["terminal", q.settlements.terminal],
    ["oldestInFlightAt", q.settlements.oldestInFlightAt ?? "—"],
    ["journalSizeBytes", q.settlements.journalSizeBytes],
  ]);
  return 0;
}

export function runMetrics(options: Record<string, string | boolean>): number {
  const { paths } = loadConfig();
  const r = new MetricsRegistry();
  const snap = r.snapshot(paths.stateDir);
  if (boolFlag(options, "json", false)) {
    process.stdout.write(safeStringify(snap, { indent: 2 }) + "\n");
    return 0;
  }
  header("Counters");
  kv(
    Object.entries(snap.counters).map(([k, v]) => [k, v as number]) as [string, number][],
  );
  header("Jobs");
  kv([
    ["total", snap.jobs.total],
    ["inFlight", snap.jobs.inFlight],
    ["terminal", snap.jobs.terminal],
  ]);
  header("Settlements");
  kv([
    ["total", snap.settlements.total],
    ["paidTotalANM", snap.settlements.paidTotalFormatted],
  ]);
  return 0;
}

export async function runDoctorUsefulWork(options: Record<string, string | boolean>): Promise<number> {
  const { config, paths } = loadConfig();
  const coordinatorUrl = stringFlag(options, "coordinator-url");
  const r = await doctorUsefulWork(config, {
    stateDir: paths.stateDir,
    coordinator: coordinatorUrl ? { baseUrl: coordinatorUrl } : undefined,
  });
  if (boolFlag(options, "json", false)) {
    process.stdout.write(safeStringify(r, { indent: 2 }) + "\n");
    return r.ok ? 0 : 1;
  }
  header(`Useful-work doctor — ${r.ok ? c.green("OK") : c.yellow("ISSUES")}`);
  for (const check of r.checks) {
    info(`  ${check.ok ? c.green("✓") : check.level === "error" ? c.red("✗") : c.yellow("!")} ${check.name}: ${check.message}`);
  }
  info("");
  info(r.summary);
  return r.ok ? 0 : 1;
}

export async function runHybridPlan(options: Record<string, string | boolean>): Promise<number> {
  const { config } = loadConfig();
  const plan = await planFromConfig(config);
  if (boolFlag(options, "json", false)) {
    process.stdout.write(safeStringify(plan, { indent: 2 }) + "\n");
    return 0;
  }
  header(`Hybrid plan: ${plan.mode}`);
  kv([
    ["chainWorkers", plan.chainWorkers],
    ["usefulWorkers", plan.usefulWorkers],
    ["usefulGpuSlots", plan.usefulGpuSlots],
    ["rationale", plan.rationale],
  ]);
  return 0;
}

export async function runCoordinatorDoctor(options: Record<string, string | boolean>): Promise<number> {
  const url = stringFlag(options, "url");
  if (!url) {
    fail("usage: animica-agent coordinator doctor --url <baseUrl>");
    return 64;
  }
  const r = await coordinatorDoctor({ baseUrl: url });
  if (boolFlag(options, "json", false)) {
    process.stdout.write(safeStringify(r, { indent: 2 }) + "\n");
    return r.ok ? 0 : 1;
  }
  header(`Coordinator doctor — ${r.ok ? c.green("OK") : c.red("FAIL")}`);
  kv([
    ["baseUrl", r.baseUrl],
    ["authConfigured", r.authConfigured],
    ["health.ok", r.healthEndpoint.ok],
    ["health.status", r.healthEndpoint.status ?? r.healthEndpoint.error ?? "—"],
    ["jobs.ok", r.jobsEndpoint.ok],
    ["jobs.sampleCount", r.jobsEndpoint.sampleCount ?? r.jobsEndpoint.error ?? "—"],
  ]);
  for (const n of r.notes) info(c.dim(`  · ${n}`));
  return r.ok ? 0 : 1;
}

export function runPayoutAudit(options: Record<string, string | boolean>): number {
  const { paths } = loadConfig();
  const auditor = new PayoutAuditor(paths.stateDir);
  const list = auditor.recent(Number.parseInt(stringFlag(options, "limit", "50") as string, 10));
  if (boolFlag(options, "json", false)) {
    process.stdout.write(safeStringify(list, { indent: 2 }) + "\n");
    return 0;
  }
  header(`Payout decisions (${list.length})`);
  table(
    ["at", "receiptId", "amountRaw", "allowed", "reason"],
    list.map((d) => [d.at, d.receiptId.slice(0, 12), d.amountRaw.toString(), d.allowed ? "yes" : "no", d.reason ?? "—"]),
  );
  return 0;
}
