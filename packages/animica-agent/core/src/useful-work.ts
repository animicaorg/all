/**
 * Useful-work job board.
 *
 * Designed for AICF-style mining where miners earn rewards by producing AI
 * artifacts (evaluations, embeddings, LoRA adapters, ranked samples,
 * synthetic data) instead of burning compute on opaque PoW.
 *
 * The agent core defines:
 *   - Job, Submission, VerificationOutcome, Reward shapes
 *   - A `Coordinator` interface that wraps a remote job board over HTTP
 *   - A `LocalCoordinator` that lets us run fully offline against a local
 *     fixture file, useful for testing and for users who want to operate
 *     in a private cluster without exposing AICF endpoints
 *
 * The agent core does NOT directly mutate production models. It only ships
 * artifacts upstream and consumes "promoted" adapters / indexes that the
 * coordinator has already validated.
 */

import { existsSync, readFileSync, writeFileSync, mkdirSync, appendFileSync } from "node:fs";
import { join } from "node:path";
import { createHash, randomUUID } from "node:crypto";

import { AgentError } from "./errors.js";
import { safeParse, safeStringify } from "./safe-json.js";

export type JobKind =
  | "eval-bench"
  | "embedding"
  | "dedupe"
  | "synthetic-code"
  | "lora-finetune"
  | "rank-samples"
  | "retrieval-index"
  | "reward-scoring";

export type JobTier = "cpu-light" | "cpu-heavy" | "gpu-small" | "gpu-large";

export interface Job {
  id: string;
  kind: JobKind;
  tier: JobTier;
  /** Target model & version this job feeds into. */
  modelTarget: string;
  modelVersion: string;
  /** Pointer to data manifest (URL, IPFS cid, or local path under .animica/datasets). */
  dataManifest: string;
  /** Deterministic-ish hyperparameters. */
  hyperparams: Record<string, unknown>;
  /** Verifiers must agree within this band. */
  toleranceBps?: number;
  /** Maximum reward (smallest unit, 18 decimals). */
  rewardCapRaw: bigint;
  /** ISO timestamps. */
  publishedAt: string;
  expiresAt: string;
  /** Free-form rules text shown to miners. */
  rules: string;
}

export interface Submission {
  id: string;
  jobId: string;
  minerAddress: string;
  worker?: string;
  /** SHA-256 hex of the artifact bytes. */
  artifactHash: string;
  /** Optional pointer (URL/path/cid) that verifiers fetch. */
  artifactPointer: string;
  /** Metric value (eval score / loss / etc.) — interpretation depends on job. */
  metric: number;
  /** Wall-clock execution time on miner. */
  elapsedMs: number;
  hardwareNote?: string;
  submittedAt: string;
}

export type VerificationStatus = "pending" | "accepted" | "rejected" | "challenged";

export interface VerificationOutcome {
  submissionId: string;
  status: VerificationStatus;
  /** Multiplicative quality score (>=1.0 means better-than-baseline). */
  quality: number;
  reason: string;
  verifiers: string[];
  decidedAt?: string;
}

export interface Reward {
  id: string;
  submissionId: string;
  minerAddress: string;
  rawAmount: bigint;
  status: "estimated" | "settled" | "withheld";
  reason?: string;
  settledAt?: string;
}

/* ---------------- Coordinator interface ---------------- */

export interface Coordinator {
  readonly name: string;
  listJobs(): Promise<Job[]>;
  getJob(id: string): Promise<Job | null>;
  submit(submission: Submission): Promise<VerificationOutcome>;
  recentRewards(minerAddress: string, limit?: number): Promise<Reward[]>;
  leaderboard(modelTarget?: string, limit?: number): Promise<{ minerAddress: string; score: number }[]>;
  adapters(modelTarget: string): Promise<{ id: string; version: string; status: string; metric: number }[]>;
}

/* ---------------- Local coordinator (offline-friendly) ---------------- */

export interface LocalCoordinatorOptions {
  dataDir: string;
  fixtureJobs?: Job[];
}

export class LocalCoordinator implements Coordinator {
  public readonly name = "local";
  constructor(private readonly opts: LocalCoordinatorOptions) {
    mkdirSync(this.opts.dataDir, { recursive: true });
  }

  private get jobsFile(): string {
    return join(this.opts.dataDir, "jobs.json");
  }
  private get submissionsFile(): string {
    return join(this.opts.dataDir, "submissions.jsonl");
  }
  private get rewardsFile(): string {
    return join(this.opts.dataDir, "rewards.jsonl");
  }
  private get adaptersFile(): string {
    return join(this.opts.dataDir, "adapters.json");
  }
  private get leaderboardFile(): string {
    return join(this.opts.dataDir, "leaderboard.json");
  }

  private readJobs(): Job[] {
    if (!existsSync(this.jobsFile)) {
      if (this.opts.fixtureJobs) {
        writeFileSync(this.jobsFile, safeStringify(this.opts.fixtureJobs, { indent: 2 }) + "\n", "utf8");
        return [...this.opts.fixtureJobs];
      }
      return [];
    }
    try {
      return safeParse<Job[]>(readFileSync(this.jobsFile, "utf8"));
    } catch {
      return [];
    }
  }

  async listJobs(): Promise<Job[]> {
    const now = new Date().toISOString();
    return this.readJobs().filter((j) => j.expiresAt > now);
  }

  async getJob(id: string): Promise<Job | null> {
    return this.readJobs().find((j) => j.id === id) ?? null;
  }

  async submit(sub: Submission): Promise<VerificationOutcome> {
    const job = await this.getJob(sub.jobId);
    if (!job) throw new AgentError("JOB", `unknown job ${sub.jobId}`);

    // The local coordinator performs a deterministic but conservative
    // validation: artifactHash must be 64 hex chars, metric must be finite,
    // elapsedMs must be positive. Real verification happens upstream.
    if (!/^[0-9a-f]{64}$/.test(sub.artifactHash)) {
      return mkOutcome(sub.id, "rejected", 0, "malformed artifactHash");
    }
    if (!Number.isFinite(sub.metric)) {
      return mkOutcome(sub.id, "rejected", 0, "non-finite metric");
    }
    if (sub.elapsedMs <= 0) {
      return mkOutcome(sub.id, "rejected", 0, "non-positive elapsedMs");
    }

    appendFileSync(this.submissionsFile, safeStringify(sub) + "\n", "utf8");
    const quality = Math.max(0.5, Math.min(1.5, 1 + (sub.metric - 0.5)));
    const outcome = mkOutcome(sub.id, "accepted", quality, "passed local validation");

    // Distribute proportional reward up to job's cap, scaled by quality.
    const bps = Math.max(0, Math.min(20000, Math.round(quality * 10000)));
    const rewardRaw = (job.rewardCapRaw * BigInt(bps)) / 10000n;
    const reward: Reward = {
      id: randomUUID(),
      submissionId: sub.id,
      minerAddress: sub.minerAddress,
      rawAmount: rewardRaw,
      status: "estimated",
      reason: outcome.reason,
    };
    appendFileSync(this.rewardsFile, safeStringify(reward) + "\n", "utf8");

    return outcome;
  }

  async recentRewards(minerAddress: string, limit = 50): Promise<Reward[]> {
    if (!existsSync(this.rewardsFile)) return [];
    const lines = readFileSync(this.rewardsFile, "utf8")
      .split(/\r?\n/)
      .filter(Boolean)
      .slice(-limit * 4);
    const out: Reward[] = [];
    for (const l of lines) {
      try {
        const r = safeParse<Reward>(l);
        if (r.minerAddress === minerAddress) out.push(r);
      } catch {
        /* skip */
      }
    }
    return out.slice(-limit);
  }

  async leaderboard(modelTarget?: string, limit = 25): Promise<{ minerAddress: string; score: number }[]> {
    if (existsSync(this.leaderboardFile)) {
      try {
        const j = safeParse<{ minerAddress: string; score: number; modelTarget?: string }[]>(
          readFileSync(this.leaderboardFile, "utf8"),
        );
        return j.filter((r) => !modelTarget || r.modelTarget === modelTarget).slice(0, limit);
      } catch {
        /* fall through */
      }
    }
    // Derive from rewards if no file is present.
    if (!existsSync(this.rewardsFile)) return [];
    const totals = new Map<string, bigint>();
    for (const l of readFileSync(this.rewardsFile, "utf8").split(/\r?\n/).filter(Boolean)) {
      try {
        const r = safeParse<Reward>(l);
        totals.set(r.minerAddress, (totals.get(r.minerAddress) ?? 0n) + r.rawAmount);
      } catch {
        /* skip */
      }
    }
    return [...totals.entries()]
      .map(([minerAddress, raw]) => ({ minerAddress, score: Number(raw / 10n ** 12n) }))
      .sort((a, b) => b.score - a.score)
      .slice(0, limit);
  }

  async adapters(modelTarget: string): Promise<{ id: string; version: string; status: string; metric: number }[]> {
    if (!existsSync(this.adaptersFile)) return [];
    try {
      const all = safeParse<
        { id: string; version: string; status: string; metric: number; modelTarget: string }[]
      >(readFileSync(this.adaptersFile, "utf8"));
      return all.filter((a) => a.modelTarget === modelTarget);
    } catch {
      return [];
    }
  }
}

/* ---------------- HTTP coordinator ---------------- */

export class HttpCoordinator implements Coordinator {
  public readonly name: string;
  constructor(private readonly baseUrl: string, private readonly apiKeyEnv = "ANIMICA_AICF_KEY", name = "http") {
    this.name = name;
  }
  private headers(): Record<string, string> {
    const h: Record<string, string> = { accept: "application/json" };
    const key = process.env[this.apiKeyEnv];
    if (key) h.authorization = `Bearer ${key}`;
    return h;
  }
  private async req<T>(path: string, init?: RequestInit): Promise<T> {
    const res = await fetch(`${this.baseUrl}${path}`, {
      ...init,
      headers: { ...this.headers(), ...(init?.headers ?? {}), "content-type": "application/json" },
    });
    if (!res.ok) throw new AgentError("COORDINATOR", `HTTP ${res.status} ${res.statusText}`);
    return (await res.json()) as T;
  }
  async listJobs(): Promise<Job[]> {
    const j = await this.req<{ jobs: Job[] }>("/jobs");
    return j.jobs;
  }
  async getJob(id: string): Promise<Job | null> {
    try {
      return await this.req<Job>(`/jobs/${encodeURIComponent(id)}`);
    } catch {
      return null;
    }
  }
  async submit(submission: Submission): Promise<VerificationOutcome> {
    return this.req<VerificationOutcome>(`/jobs/${encodeURIComponent(submission.jobId)}/submissions`, {
      method: "POST",
      body: safeStringify(submission),
    });
  }
  async recentRewards(minerAddress: string, limit = 50): Promise<Reward[]> {
    const j = await this.req<{ rewards: Reward[] }>(`/rewards?miner=${encodeURIComponent(minerAddress)}&limit=${limit}`);
    return j.rewards;
  }
  async leaderboard(modelTarget?: string, limit = 25): Promise<{ minerAddress: string; score: number }[]> {
    const q = new URLSearchParams();
    if (modelTarget) q.set("model", modelTarget);
    q.set("limit", String(limit));
    const j = await this.req<{ leaderboard: { minerAddress: string; score: number }[] }>(`/leaderboard?${q}`);
    return j.leaderboard;
  }
  async adapters(modelTarget: string): Promise<{ id: string; version: string; status: string; metric: number }[]> {
    const j = await this.req<{ adapters: { id: string; version: string; status: string; metric: number }[] }>(
      `/adapters?model=${encodeURIComponent(modelTarget)}`,
    );
    return j.adapters;
  }
}

function mkOutcome(submissionId: string, status: VerificationStatus, quality: number, reason: string): VerificationOutcome {
  return { submissionId, status, quality, reason, verifiers: ["local"], decidedAt: new Date().toISOString() };
}

/** Convenience: hash a file or buffer for artifact submission. */
export function hashArtifact(buf: Buffer | Uint8Array | string): string {
  return createHash("sha256").update(buf).digest("hex");
}
