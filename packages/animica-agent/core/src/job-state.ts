/**
 * Persisted job lifecycle state machine.
 *
 * Designed for the useful-work miner runtime: every transition is logged to
 * a JSONL journal so that a process restart can recover the in-flight set
 * deterministically. The journal is append-only; the current state is the
 * last record per job id.
 *
 * The state graph below is intentionally small and explicit. Every
 * transition is allow-listed in `ALLOWED_TRANSITIONS`. An attempt to make an
 * unlisted transition throws `InvalidTransition`. There is no implicit
 * cleanup, no silent retry — operators must drive transitions explicitly
 * from a small number of well-known callsites (the runtime, the CLI, and
 * the settlement worker).
 *
 *   discovered          ─┐
 *        ↓               │
 *   accepted             │
 *        ↓               │
 *   running              │  any non-terminal state can transition to
 *        ↓               ├─►  failed   (permanent)
 *   completed            │
 *        ↓               │
 *   artifacted           │
 *        ↓               │
 *   submitted ──────►  rejected_remote
 *        ↓
 *   accepted_remote
 *        ↓
 *   settlement_pending
 *        ↓
 *   paid
 *
 * Terminal states: paid, rejected_remote, failed.
 */

import { existsSync, mkdirSync, readFileSync, writeFileSync, appendFileSync, renameSync } from "node:fs";
import { join } from "node:path";

import { AgentError } from "./errors.js";
import { safeParse, safeStringify } from "./safe-json.js";

export type JobStatus =
  | "discovered"
  | "accepted"
  | "running"
  | "completed"
  | "artifacted"
  | "submitted"
  | "accepted_remote"
  | "rejected_remote"
  | "settlement_pending"
  | "paid"
  | "failed";

export const TERMINAL_STATUSES: ReadonlySet<JobStatus> = new Set([
  "paid",
  "rejected_remote",
  "failed",
]);

/**
 * Allowed transitions.
 *
 * In addition to the documented forward path, both `accepted` and `running`
 * permit a reverse edge to `discovered`. This is the **only** form of state
 * regression we allow, and it exists solely for restart recovery: when a
 * process crashes mid-run, the journal-replay handler resets in-flight
 * work back to `discovered` so the next iteration claims and re-runs it.
 * Past `artifacted` we never rewind, because re-submission with the same
 * idempotency key is preferred over re-execution.
 */
export const ALLOWED_TRANSITIONS: Readonly<Record<JobStatus, ReadonlySet<JobStatus>>> = Object.freeze({
  discovered: new Set<JobStatus>(["accepted", "failed"]),
  accepted: new Set<JobStatus>(["running", "discovered", "failed"]),
  running: new Set<JobStatus>(["completed", "discovered", "failed"]),
  completed: new Set<JobStatus>(["artifacted", "failed"]),
  artifacted: new Set<JobStatus>(["submitted", "failed"]),
  submitted: new Set<JobStatus>(["accepted_remote", "rejected_remote", "failed"]),
  accepted_remote: new Set<JobStatus>(["settlement_pending", "paid", "failed"]),
  rejected_remote: new Set<JobStatus>([]), // terminal
  settlement_pending: new Set<JobStatus>(["paid", "failed"]),
  paid: new Set<JobStatus>([]), // terminal
  failed: new Set<JobStatus>([]), // terminal
});

export type FailureClass = "transient" | "permanent" | "unknown";

export interface JobStateRecord {
  /** Stable id provided by the coordinator (Job.id). */
  jobId: string;
  /** Per-submission idempotency key. Stable across retries. */
  idempotencyKey: string;
  /** Current state. */
  status: JobStatus;
  /** Most recent transition timestamp. */
  updatedAt: string;
  /** First time we saw this job. */
  createdAt: string;
  /** Optional details about the most recent transition. */
  reason?: string;
  /** Number of times the runtime attempted to run this job. */
  attempts: number;
  /** Classification of the latest failure, if any. */
  failureClass?: FailureClass;
  /** Pointer to the artifact on disk (set when status reaches artifacted). */
  artifactPath?: string;
  /** Artifact hash (set when status reaches artifacted). */
  artifactHash?: string;
  /** Reward receipt id (set when status reaches accepted_remote or later). */
  receiptId?: string;
  /** Optional settlement tx hash. */
  txHash?: string;
  /** Worker tag (worker name) and payout address attribution. */
  workerName?: string;
  minerAddress?: string;
  /** Free-form labels (kind, modelTarget, etc.). */
  labels?: Record<string, string>;
}

export class InvalidTransition extends AgentError {
  constructor(from: JobStatus, to: JobStatus) {
    super("INVALID_TRANSITION", `cannot transition from ${from} to ${to}`);
    this.name = "InvalidTransition";
  }
}

export function canTransition(from: JobStatus, to: JobStatus): boolean {
  // Idempotent same-state transition is always allowed.
  if (from === to) return true;
  return ALLOWED_TRANSITIONS[from].has(to);
}

/**
 * Persisted JSONL-backed state store. One file per directory; each line is a
 * full snapshot of a JobStateRecord. The current state for a given jobId is
 * the last record with that jobId (allowing journal replay).
 *
 * We deliberately do not normalize/compact the file at runtime; operators
 * can run `compact()` offline. JSONL with last-write-wins keeps the writer
 * lock-free and crash-safe.
 */
export class JobStateStore {
  private readonly journalFile: string;
  /** In-memory current-state cache, indexed by jobId. */
  private cache: Map<string, JobStateRecord> = new Map();
  private loaded = false;

  constructor(stateDir: string) {
    mkdirSync(stateDir, { recursive: true });
    this.journalFile = join(stateDir, "jobs.jsonl");
  }

  /** Force a journal reload. Idempotent. */
  reload(): void {
    this.cache.clear();
    if (!existsSync(this.journalFile)) {
      this.loaded = true;
      return;
    }
    const text = readFileSync(this.journalFile, "utf8");
    for (const line of text.split(/\r?\n/)) {
      if (!line) continue;
      try {
        const rec = safeParse<JobStateRecord>(line);
        if (rec && typeof rec === "object" && rec.jobId) {
          this.cache.set(rec.jobId, rec);
        }
      } catch {
        /* skip corrupt lines so a partial write doesn't break recovery */
      }
    }
    this.loaded = true;
  }

  private ensureLoaded(): void {
    if (!this.loaded) this.reload();
  }

  get(jobId: string): JobStateRecord | null {
    this.ensureLoaded();
    return this.cache.get(jobId) ?? null;
  }

  list(): JobStateRecord[] {
    this.ensureLoaded();
    return [...this.cache.values()];
  }

  /** Records that are not in a terminal state. Used by restart recovery. */
  listInFlight(): JobStateRecord[] {
    return this.list().filter((r) => !TERMINAL_STATUSES.has(r.status));
  }

  /** Idempotently create a discovered record. Returns the active record. */
  discover(input: Pick<JobStateRecord, "jobId" | "idempotencyKey" | "workerName" | "minerAddress" | "labels">): JobStateRecord {
    this.ensureLoaded();
    const existing = this.cache.get(input.jobId);
    if (existing) return existing;
    const now = new Date().toISOString();
    const rec: JobStateRecord = {
      ...input,
      status: "discovered",
      createdAt: now,
      updatedAt: now,
      attempts: 0,
    };
    this.append(rec);
    return rec;
  }

  /**
   * Transition a job. Validates the move and persists the new record.
   * Returns the updated record. Throws InvalidTransition on illegal moves.
   */
  transition(jobId: string, to: JobStatus, patch: Partial<JobStateRecord> = {}): JobStateRecord {
    this.ensureLoaded();
    const cur = this.cache.get(jobId);
    if (!cur) {
      throw new AgentError("UNKNOWN_JOB", `no state record for jobId=${jobId}`);
    }
    if (!canTransition(cur.status, to)) {
      throw new InvalidTransition(cur.status, to);
    }
    const next: JobStateRecord = {
      ...cur,
      ...patch,
      jobId: cur.jobId,
      idempotencyKey: cur.idempotencyKey,
      status: to,
      updatedAt: new Date().toISOString(),
      attempts: to === "running" ? cur.attempts + 1 : cur.attempts,
    };
    this.append(next);
    return next;
  }

  /** Append a record (caller's responsibility to validate). */
  private append(rec: JobStateRecord): void {
    this.cache.set(rec.jobId, rec);
    appendFileSync(this.journalFile, safeStringify(rec) + "\n", "utf8");
  }

  /**
   * Compact the journal by rewriting it with only the latest record per jobId.
   * Safe to call offline; uses an atomic rename. Returns the number of lines
   * removed.
   */
  compact(): number {
    this.ensureLoaded();
    if (!existsSync(this.journalFile)) return 0;
    const beforeLines = readFileSync(this.journalFile, "utf8").split(/\r?\n/).filter(Boolean).length;
    const tmp = this.journalFile + ".tmp";
    const body = [...this.cache.values()].map((r) => safeStringify(r)).join("\n") + "\n";
    writeFileSync(tmp, body, "utf8");
    renameSync(tmp, this.journalFile);
    return Math.max(0, beforeLines - this.cache.size);
  }

  /** For tests and CLI use; the on-disk path. */
  path(): string {
    return this.journalFile;
  }
}

/** Conservative failure classifier shared by the runtime and the signer wrappers. */
export function classifyJobFailure(messageOrReason: string | undefined): FailureClass {
  if (!messageOrReason) return "unknown";
  const s = messageOrReason.toLowerCase();
  // Permanent: never going to succeed by retrying.
  if (/(?:malformed|invalid|unsupported|insufficient|not authorized|bad chain|chain ?id mismatch|wallet not found)/.test(s))
    return "permanent";
  // Transient: should retry.
  if (/(?:timeout|timed[ -]?out|etimedout|econn|enotfound|temporarily|rate ?limit|busy|service unavailable|503|502|429|fetch failed)/.test(s))
    return "transient";
  return "unknown";
}
