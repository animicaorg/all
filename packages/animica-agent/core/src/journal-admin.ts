/**
 * Operator journal-admin helpers.
 *
 * Compaction and pruning utilities for the useful-work miner's persisted
 * journals. All operations are conservative:
 *   - compaction writes to a temp file then renames atomically
 *   - pruning archives removed records to a sibling file rather than
 *     deleting them outright
 *   - terminal-only operations refuse to touch in-flight records
 *
 * These are designed to be invoked from a CLI; nothing here mutates
 * runtime state without an explicit operator call.
 */

import { existsSync, mkdirSync, readFileSync, renameSync, writeFileSync, appendFileSync, statSync } from "node:fs";
import { join } from "node:path";

import { JobStateStore, TERMINAL_STATUSES, type JobStateRecord, type JobStatus } from "./job-state.js";
import { safeStringify } from "./safe-json.js";
import { SettlementJournal, SETTLEMENT_TERMINAL_STATES, type SettlementAttempt } from "./settlement-engine.js";

export interface CompactionReport {
  file: string;
  beforeLines: number;
  afterLines: number;
  dropped: number;
}

/**
 * Compact a JobStateStore's journal. After compaction the file contains
 * exactly one record per jobId: the latest state.
 */
export function compactJobs(stateDir: string): CompactionReport {
  const store = new JobStateStore(stateDir);
  store.reload();
  const path = store.path();
  const beforeLines = countLines(path);
  const dropped = store.compact();
  const afterLines = countLines(path);
  return { file: path, beforeLines, afterLines, dropped };
}

export function compactSettlements(stateDir: string): CompactionReport {
  const journal = new SettlementJournal(stateDir);
  journal.reload();
  const path = journal.path();
  const beforeLines = countLines(path);
  const dropped = journal.compact();
  const afterLines = countLines(path);
  return { file: path, beforeLines, afterLines, dropped };
}

export interface ArchiveReport<T> {
  archived: T[];
  archivedFile: string;
  remaining: number;
}

/**
 * Move terminal failed jobs to an archive file. The archive is append-only
 * so an operator can later diff archived state against the live journal.
 *
 * `olderThanMs` is required (no default) so callers explicitly opt into
 * the time horizon and never sweep recent failures by mistake.
 */
export function archiveFailedJobs(
  stateDir: string,
  options: { olderThanMs: number; reasonsLike?: string },
): ArchiveReport<JobStateRecord> {
  const store = new JobStateStore(stateDir);
  store.reload();
  const all = store.list();
  const cutoff = Date.now() - options.olderThanMs;
  const filter = (rec: JobStateRecord) =>
    rec.status === "failed" &&
    new Date(rec.updatedAt).getTime() < cutoff &&
    (!options.reasonsLike || (rec.reason ?? "").includes(options.reasonsLike));

  const toArchive = all.filter(filter);
  const remainingRecords = all.filter((r) => !filter(r));
  const archiveDir = join(stateDir, "archive");
  mkdirSync(archiveDir, { recursive: true });
  const archivedFile = join(archiveDir, `jobs-archived-${new Date().toISOString().slice(0, 10)}.jsonl`);
  for (const r of toArchive) {
    appendFileSync(archivedFile, safeStringify(r) + "\n", "utf8");
  }
  // Rewrite the live journal with just the remaining records.
  const live = store.path();
  const tmp = live + ".tmp";
  writeFileSync(
    tmp,
    remainingRecords.map((r) => safeStringify(r)).join("\n") + (remainingRecords.length ? "\n" : ""),
    "utf8",
  );
  renameSync(tmp, live);
  return { archived: toArchive, archivedFile, remaining: remainingRecords.length };
}

/** Inspector — operator-facing summary without mutating anything. */
export interface QueueInspection {
  jobs: {
    total: number;
    inFlight: number;
    terminal: number;
    oldestInFlightAt?: string;
    oldestInFlightAgeMs?: number;
    statuses: Record<JobStatus, number>;
    journalSizeBytes: number;
  };
  settlements: {
    total: number;
    inFlight: number;
    terminal: number;
    oldestInFlightAt?: string;
    oldestInFlightAgeMs?: number;
    journalSizeBytes: number;
  };
}

export function inspectQueues(stateDir: string): QueueInspection {
  const store = new JobStateStore(stateDir);
  store.reload();
  const jobs = store.list();
  const journal = new SettlementJournal(stateDir);
  journal.reload();
  const settlements = journal.list();

  const statuses: Record<JobStatus, number> = {
    discovered: 0,
    accepted: 0,
    running: 0,
    completed: 0,
    artifacted: 0,
    submitted: 0,
    accepted_remote: 0,
    rejected_remote: 0,
    settlement_pending: 0,
    paid: 0,
    failed: 0,
  };
  let jobsInFlight = 0;
  let jobsTerminal = 0;
  let oldestJobsAt: string | undefined;
  for (const j of jobs) {
    statuses[j.status] = (statuses[j.status] ?? 0) + 1;
    if (TERMINAL_STATUSES.has(j.status)) jobsTerminal++;
    else {
      jobsInFlight++;
      if (!oldestJobsAt || j.updatedAt < oldestJobsAt) oldestJobsAt = j.updatedAt;
    }
  }

  let settInFlight = 0;
  let settTerminal = 0;
  let oldestSettAt: string | undefined;
  for (const a of settlements) {
    if (SETTLEMENT_TERMINAL_STATES.has(a.status)) settTerminal++;
    else {
      settInFlight++;
      if (!oldestSettAt || a.updatedAt < oldestSettAt) oldestSettAt = a.updatedAt;
    }
  }

  return {
    jobs: {
      total: jobs.length,
      inFlight: jobsInFlight,
      terminal: jobsTerminal,
      oldestInFlightAt: oldestJobsAt,
      oldestInFlightAgeMs: oldestJobsAt ? Date.now() - new Date(oldestJobsAt).getTime() : undefined,
      statuses,
      journalSizeBytes: existsSync(store.path()) ? statSync(store.path()).size : 0,
    },
    settlements: {
      total: settlements.length,
      inFlight: settInFlight,
      terminal: settTerminal,
      oldestInFlightAt: oldestSettAt,
      oldestInFlightAgeMs: oldestSettAt ? Date.now() - new Date(oldestSettAt).getTime() : undefined,
      journalSizeBytes: existsSync(journal.path()) ? statSync(journal.path()).size : 0,
    },
  };
}

function countLines(path: string): number {
  if (!existsSync(path)) return 0;
  return readFileSync(path, "utf8").split(/\r?\n/).filter(Boolean).length;
}
