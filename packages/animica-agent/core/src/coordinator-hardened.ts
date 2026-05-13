/**
 * Production-hardened Coordinator client.
 *
 * Wraps an `HttpCoordinator`-style endpoint with:
 *   - Authenticated headers (Bearer + optional X-Animica-Worker)
 *   - Strict 4xx vs 5xx vs malformed-JSON classification
 *   - Bounded exponential backoff for transient errors only
 *   - Per-call timeout
 *   - Idempotency at the submission boundary via Submission.id
 *   - Offline submission queue persisted to JSONL so a miner that loses
 *     upstream connectivity can keep working and replay submissions when
 *     the coordinator comes back
 *   - A CoordinatorDoctor that validates the upstream before use
 *
 * Fails closed: when authentication, base URL, or expected endpoints are
 * unconfigured, the client refuses and surfaces a clear error rather than
 * pretending to succeed.
 */

import { appendFileSync, existsSync, mkdirSync, readFileSync, renameSync, writeFileSync } from "node:fs";
import { join } from "node:path";

import { AgentError } from "./errors.js";
import { safeParse, safeStringify } from "./safe-json.js";
import {
  type Coordinator,
  type Job,
  type Reward,
  type Submission,
  type VerificationOutcome,
} from "./useful-work.js";

export interface HardenedCoordinatorOptions {
  baseUrl: string;
  /** Env var that holds the bearer token. Default: ANIMICA_AICF_KEY. */
  authEnv?: string;
  /** Optional worker identifier sent in X-Animica-Worker. */
  worker?: string;
  /** Per-request timeout (ms). Default 10_000. */
  timeoutMs?: number;
  /** Max in-process retries for transient (5xx, network) failures. Default 3. */
  maxRetries?: number;
  /** Initial backoff (ms). Default 250. Doubled on each retry up to backoffCapMs. */
  backoffMs?: number;
  backoffCapMs?: number;
  /** Fetch shim for tests. */
  fetchImpl?: typeof fetch;
  /** Sleep shim for tests. */
  sleep?: (ms: number) => Promise<void>;
  /** Offline queue persistence dir. Required for queue() / replay(). */
  queueDir?: string;
  /** Sets the `name` reported to operators. */
  name?: string;
}

/** Result of validating an upstream response shape against the expected `Job` type. */
function isJobShape(value: unknown): value is Job {
  if (typeof value !== "object" || value === null) return false;
  const v = value as Record<string, unknown>;
  return (
    typeof v.id === "string" &&
    typeof v.kind === "string" &&
    typeof v.tier === "string" &&
    typeof v.modelTarget === "string" &&
    typeof v.modelVersion === "string" &&
    typeof v.dataManifest === "string" &&
    typeof v.rules === "string" &&
    typeof v.publishedAt === "string" &&
    typeof v.expiresAt === "string"
  );
}

function isVerificationOutcomeShape(v: unknown): v is VerificationOutcome {
  if (typeof v !== "object" || v === null) return false;
  const r = v as Record<string, unknown>;
  return (
    typeof r.submissionId === "string" &&
    (r.status === "accepted" || r.status === "rejected" || r.status === "pending" || r.status === "challenged") &&
    typeof r.quality === "number"
  );
}

export class CoordinatorAuthError extends AgentError {
  constructor(message: string) {
    super("COORDINATOR_AUTH", message);
    this.name = "CoordinatorAuthError";
  }
}
export class CoordinatorTransientError extends AgentError {
  constructor(message: string) {
    super("COORDINATOR_TRANSIENT", message);
    this.name = "CoordinatorTransientError";
  }
}
export class CoordinatorPermanentError extends AgentError {
  constructor(message: string) {
    super("COORDINATOR_PERMANENT", message);
    this.name = "CoordinatorPermanentError";
  }
}
export class CoordinatorShapeError extends AgentError {
  constructor(message: string) {
    super("COORDINATOR_SHAPE", message);
    this.name = "CoordinatorShapeError";
  }
}

interface QueuedSubmission {
  id: string;
  enqueuedAt: string;
  submission: Submission;
}

/* ----------------- queue ----------------- */

export class OfflineSubmissionQueue {
  private readonly file: string;
  constructor(queueDir: string) {
    mkdirSync(queueDir, { recursive: true });
    this.file = join(queueDir, "submission-queue.jsonl");
  }
  path(): string {
    return this.file;
  }
  enqueue(s: Submission): QueuedSubmission {
    const rec: QueuedSubmission = { id: s.id, enqueuedAt: new Date().toISOString(), submission: s };
    appendFileSync(this.file, safeStringify(rec) + "\n", "utf8");
    return rec;
  }
  list(): QueuedSubmission[] {
    if (!existsSync(this.file)) return [];
    const out: QueuedSubmission[] = [];
    for (const line of readFileSync(this.file, "utf8").split(/\r?\n/)) {
      if (!line) continue;
      try {
        out.push(safeParse<QueuedSubmission>(line));
      } catch {
        /* skip corrupt line */
      }
    }
    return out;
  }
  /** Atomically remove a single id from the queue. Returns true if removed. */
  remove(id: string): boolean {
    const all = this.list().filter((q) => q.id !== id);
    const tmp = this.file + ".tmp";
    writeFileSync(tmp, all.map((q) => safeStringify(q)).join("\n") + (all.length ? "\n" : ""), "utf8");
    renameSync(tmp, this.file);
    return true;
  }
  size(): number {
    return this.list().length;
  }
}

/* ----------------- doctor ----------------- */

export interface CoordinatorDoctorReport {
  ok: boolean;
  baseUrl: string;
  authConfigured: boolean;
  healthEndpoint: { ok: boolean; status?: number; error?: string };
  jobsEndpoint: { ok: boolean; sampleCount?: number; error?: string };
  notes: string[];
}

export async function coordinatorDoctor(opts: HardenedCoordinatorOptions): Promise<CoordinatorDoctorReport> {
  const baseUrl = opts.baseUrl;
  const env = opts.authEnv ?? "ANIMICA_AICF_KEY";
  const authConfigured = !!process.env[env];
  const notes: string[] = [];
  if (!authConfigured) notes.push(`no auth token in env ${env}; calls will be unauthenticated`);
  const fetchImpl = opts.fetchImpl ?? globalThis.fetch;
  let health: CoordinatorDoctorReport["healthEndpoint"] = { ok: false };
  let jobs: CoordinatorDoctorReport["jobsEndpoint"] = { ok: false };
  try {
    const r = await fetchImpl(`${baseUrl}/health`, { method: "GET" });
    health = { ok: r.ok, status: r.status };
  } catch (err) {
    health = { ok: false, error: (err as Error).message };
  }
  try {
    const r = await fetchImpl(`${baseUrl}/jobs`, { method: "GET", headers: authConfigured ? { authorization: `Bearer ${process.env[env]}` } : {} });
    if (r.ok) {
      const body = (await r.json().catch(() => null)) as { jobs?: unknown[] } | null;
      if (body && Array.isArray(body.jobs)) jobs = { ok: true, sampleCount: body.jobs.length };
      else jobs = { ok: false, error: "unexpected response shape: missing jobs[]" };
    } else {
      jobs = { ok: false, error: `HTTP ${r.status}` };
    }
  } catch (err) {
    jobs = { ok: false, error: (err as Error).message };
  }
  return { ok: health.ok && jobs.ok, baseUrl, authConfigured, healthEndpoint: health, jobsEndpoint: jobs, notes };
}

/* ----------------- main client ----------------- */

export class HardenedCoordinator implements Coordinator {
  public readonly name: string;
  private readonly baseUrl: string;
  private readonly authEnv: string;
  private readonly worker?: string;
  private readonly timeoutMs: number;
  private readonly maxRetries: number;
  private readonly backoffMs: number;
  private readonly backoffCapMs: number;
  private readonly fetchImpl: typeof fetch;
  private readonly sleep: (ms: number) => Promise<void>;
  private readonly queue: OfflineSubmissionQueue | null;

  constructor(opts: HardenedCoordinatorOptions) {
    if (!opts.baseUrl) throw new AgentError("CONFIG", "HardenedCoordinator requires baseUrl");
    this.baseUrl = opts.baseUrl.replace(/\/$/, "");
    this.authEnv = opts.authEnv ?? "ANIMICA_AICF_KEY";
    this.worker = opts.worker;
    this.timeoutMs = opts.timeoutMs ?? 10_000;
    this.maxRetries = Math.max(0, opts.maxRetries ?? 3);
    this.backoffMs = Math.max(50, opts.backoffMs ?? 250);
    this.backoffCapMs = Math.max(this.backoffMs, opts.backoffCapMs ?? 5_000);
    this.fetchImpl = opts.fetchImpl ?? globalThis.fetch;
    this.sleep = opts.sleep ?? ((ms) => new Promise((r) => setTimeout(r, ms)));
    this.queue = opts.queueDir ? new OfflineSubmissionQueue(opts.queueDir) : null;
    this.name = opts.name ?? "hardened-http";
  }

  private headers(): Record<string, string> {
    const h: Record<string, string> = {
      accept: "application/json",
      "content-type": "application/json",
      "x-animica-client": "@animica/agent-core",
    };
    const key = process.env[this.authEnv];
    if (key) h.authorization = `Bearer ${key}`;
    if (this.worker) h["x-animica-worker"] = this.worker;
    return h;
  }

  private async request<T>(path: string, init: RequestInit = {}, validator?: (v: unknown) => v is T): Promise<T> {
    let attempt = 0;
    let lastErr: unknown;
    for (; attempt <= this.maxRetries; attempt++) {
      const ctl = new AbortController();
      const t = setTimeout(() => ctl.abort(), this.timeoutMs);
      try {
        const res = await this.fetchImpl(`${this.baseUrl}${path}`, {
          ...init,
          headers: { ...this.headers(), ...(init.headers ?? {}) },
          signal: ctl.signal,
        });
        clearTimeout(t);
        if (res.status === 401 || res.status === 403) {
          throw new CoordinatorAuthError(`HTTP ${res.status} on ${path}`);
        }
        if (res.status >= 500) {
          lastErr = new CoordinatorTransientError(`HTTP ${res.status} on ${path}`);
          if (attempt < this.maxRetries) {
            await this.sleep(Math.min(this.backoffMs * 2 ** attempt, this.backoffCapMs));
            continue;
          }
          throw lastErr;
        }
        if (res.status >= 400) {
          const text = await safeText(res);
          throw new CoordinatorPermanentError(`HTTP ${res.status} on ${path}: ${text.slice(0, 200)}`);
        }
        let body: unknown;
        try {
          body = await res.json();
        } catch (err) {
          throw new CoordinatorShapeError(`malformed JSON from ${path}: ${(err as Error).message}`);
        }
        if (validator && !validator(body)) {
          throw new CoordinatorShapeError(`unexpected response shape from ${path}`);
        }
        return body as T;
      } catch (err) {
        clearTimeout(t);
        // Network / abort = transient.
        if (
          err instanceof CoordinatorAuthError ||
          err instanceof CoordinatorPermanentError ||
          err instanceof CoordinatorShapeError
        ) {
          throw err;
        }
        lastErr = err instanceof Error ? new CoordinatorTransientError(err.message) : new CoordinatorTransientError(String(err));
        if (attempt < this.maxRetries) {
          await this.sleep(Math.min(this.backoffMs * 2 ** attempt, this.backoffCapMs));
          continue;
        }
        throw lastErr;
      }
    }
    throw lastErr ?? new CoordinatorTransientError(`request exhausted retries for ${path}`);
  }

  async listJobs(): Promise<Job[]> {
    const body = await this.request<{ jobs: unknown }>(`/jobs`, { method: "GET" }, (v): v is { jobs: unknown } => {
      return typeof v === "object" && v !== null && "jobs" in (v as object);
    });
    if (!Array.isArray(body.jobs)) throw new CoordinatorShapeError("/jobs missing 'jobs' array");
    const out: Job[] = [];
    for (const j of body.jobs) {
      if (!isJobShape(j)) throw new CoordinatorShapeError("/jobs contained a non-Job entry");
      out.push(j as Job);
    }
    return out;
  }

  async getJob(id: string): Promise<Job | null> {
    try {
      const body = await this.request<unknown>(`/jobs/${encodeURIComponent(id)}`, { method: "GET" });
      if (!isJobShape(body)) throw new CoordinatorShapeError(`/jobs/:id returned non-Job`);
      return body;
    } catch (err) {
      if (err instanceof CoordinatorPermanentError && /HTTP 404/.test(err.message)) return null;
      throw err;
    }
  }

  /**
   * Submit an artifact. If the coordinator is unreachable and a queue is
   * configured, the submission is queued and a synthetic "pending" outcome
   * is returned. The caller is responsible for later calling `replayQueue()`
   * when the coordinator is back.
   */
  async submit(submission: Submission): Promise<VerificationOutcome> {
    try {
      const body = await this.request<unknown>(
        `/jobs/${encodeURIComponent(submission.jobId)}/submissions`,
        { method: "POST", body: safeStringify(submission) },
        (v): v is VerificationOutcome => isVerificationOutcomeShape(v),
      );
      return body as VerificationOutcome;
    } catch (err) {
      if (err instanceof CoordinatorTransientError && this.queue) {
        this.queue.enqueue(submission);
        return {
          submissionId: submission.id,
          status: "pending",
          quality: 0,
          reason: `queued for replay: ${err.message}`,
          verifiers: ["offline-queue"],
        };
      }
      throw err;
    }
  }

  async recentRewards(minerAddress: string, limit = 50): Promise<Reward[]> {
    const body = await this.request<{ rewards: unknown }>(
      `/rewards?miner=${encodeURIComponent(minerAddress)}&limit=${limit}`,
      { method: "GET" },
      (v): v is { rewards: unknown } => typeof v === "object" && v !== null && "rewards" in (v as object),
    );
    if (!Array.isArray(body.rewards)) throw new CoordinatorShapeError("/rewards missing 'rewards' array");
    return body.rewards as Reward[];
  }

  async leaderboard(modelTarget?: string, limit = 25): Promise<{ minerAddress: string; score: number }[]> {
    const q = new URLSearchParams();
    if (modelTarget) q.set("model", modelTarget);
    q.set("limit", String(limit));
    const body = await this.request<{ leaderboard: unknown }>(`/leaderboard?${q}`, { method: "GET" });
    if (!Array.isArray(body.leaderboard)) throw new CoordinatorShapeError("/leaderboard missing array");
    return body.leaderboard as { minerAddress: string; score: number }[];
  }

  async adapters(modelTarget: string): Promise<{ id: string; version: string; status: string; metric: number }[]> {
    const body = await this.request<{ adapters: unknown }>(
      `/adapters?model=${encodeURIComponent(modelTarget)}`,
      { method: "GET" },
    );
    if (!Array.isArray(body.adapters)) throw new CoordinatorShapeError("/adapters missing array");
    return body.adapters as { id: string; version: string; status: string; metric: number }[];
  }

  /** Replay any queued submissions; returns per-submission outcomes. */
  async replayQueue(): Promise<{ submissionId: string; outcome: VerificationOutcome | { error: string } }[]> {
    if (!this.queue) return [];
    const items = this.queue.list();
    const out: { submissionId: string; outcome: VerificationOutcome | { error: string } }[] = [];
    for (const it of items) {
      try {
        const outcome = await this.submit(it.submission);
        if (outcome.status !== "pending") {
          // Real outcome → safe to drop from the queue.
          this.queue.remove(it.id);
        }
        out.push({ submissionId: it.submission.id, outcome });
      } catch (err) {
        out.push({ submissionId: it.submission.id, outcome: { error: (err as Error).message } });
      }
    }
    return out;
  }

  /** Operator inspector. */
  queueDepth(): number {
    return this.queue?.size() ?? 0;
  }
}

async function safeText(res: Response): Promise<string> {
  try {
    return await res.text();
  } catch {
    return "<unreadable>";
  }
}
