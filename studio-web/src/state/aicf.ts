/**
 * AICF slice — enqueue AI/Quantum jobs and track their lifecycle & results.
 *
 * Integrates with studio-web services layer (../services/aicf.ts), which should provide:
 *  - enqueueAIJob(spec)         → { jobId }
 *  - enqueueQuantumJob(spec)    → { jobId }
 *  - getJob(jobId)              → JobRecord
 *  - listJobs(params?)          → { items: JobRecord[], next?: string }
 *  - getResult(resultId)        → ResultRecord
 *
 * This slice manages many jobs concurrently. It keeps a dictionary by id and a small
 * polling loop that refreshes any job that is not in a terminal state.
 */

import { registerSlice, type SliceCreator, type StoreState, type SetState, type GetState } from './store';
import * as AicfSvc from '../services/aicf';

export type JobKind = 'ai' | 'quantum';

export type JobStatus =
  | 'queued'
  | 'assigned'
  | 'leased'
  | 'running'
  | 'completed'
  | 'failed'
  | 'expired'
  | 'canceled'
  | 'unknown';

export interface AIJobSpec {
  model: string;
  prompt: string;
  maxTokens?: number;
  temperature?: number;
  aiUnits?: number;          // optional unit hint for pricing
  fee?: string | number;     // max fee willing to pay (chain units)
  tags?: string[];
}

export interface QuantumJobSpec {
  circuit: any;              // JSON circuit description
  shots: number;
  depth?: number;
  width?: number;
  traps?: number;            // trap-circuit count for SLA
  quantumUnits?: number;     // optional unit hint for pricing
  fee?: string | number;
  tags?: string[];
}

export interface JobRecord {
  id: string;
  kind: JobKind;
  status: JobStatus;
  createdAt?: string;
  updatedAt?: string;
  requester?: string;        // address (optional)
  providerId?: string;
  leaseExpiresAt?: string;
  resultId?: string;
  cost?: string;             // in chain units
  error?: string;
  meta?: Record<string, unknown>;
}

export interface ResultRecord {
  id: string;
  kind: JobKind;
  taskId?: string;           // deterministic task id (if exposed)
  outputDigest?: string;     // optional digest of output blob
  data?: any;                // result payload (truncated/summary per service)
  metrics?: Record<string, unknown>;
  verified?: boolean;        // whether on-chain proof matched
  completedAt?: string;
}

type PollingState = {
  _reqGen: number;
  _timer?: any;
  _intervalMs: number;
};

export interface AICFSlice extends PollingState {
  jobs: Record<string, JobRecord>;
  results: Record<string, ResultRecord>;
  submitting?: boolean;
  lastList?: string[];         // last page of job ids
  nextCursor?: string;         // pagination cursor (if any)
  error?: string;

  // actions
  reset(): void;

  enqueueAI(spec: AIJobSpec): Promise<string | undefined>;
  enqueueQuantum(spec: QuantumJobSpec): Promise<string | undefined>;

  refresh(jobId: string): Promise<JobRecord | undefined>;
  fetchResult(resultId: string): Promise<ResultRecord | undefined>;

  list(params?: { cursor?: string; limit?: number; requester?: string }): Promise<string[]>;

  startPolling(): void;
  stopPolling(): void;
}

// ---------- helpers ----------

function terminal(status: JobStatus): boolean {
  return status === 'completed' || status === 'failed' || status === 'expired' || status === 'canceled';
}

function safeSvc<T extends keyof typeof AicfSvc>(k: T): (typeof AicfSvc)[T] | undefined {
  const fn = (AicfSvc as any)[k];
  return typeof fn === 'function' ? fn : undefined;
}

function mergeJob(oldJ?: JobRecord, nxt?: Partial<JobRecord>): JobRecord | undefined {
  if (!oldJ && !nxt) return undefined;
  const base: JobRecord = oldJ ?? {
    id: (nxt?.id as string) || '',
    kind: (nxt?.kind as JobKind) || 'ai',
    status: (nxt?.status as JobStatus) || 'unknown',
  };
  const merged: JobRecord = {
    ...base,
    ...nxt,
    // prefer most recent timestamps if provided
    updatedAt: nxt?.updatedAt ?? base.updatedAt,
  };
  if (!merged.id && nxt?.id) merged.id = nxt.id;
  return merged;
}

// ---------- slice ----------

const createAICFSlice: SliceCreator<AICFSlice> = (set: SetState<StoreState>, get: GetState<StoreState>) => ({
  jobs: {},
  results: {},
  submitting: false,
  lastList: [],
  nextCursor: undefined,
  error: undefined,

  _reqGen: 0,
  _timer: undefined,
  _intervalMs: 1200,

  reset() {
    const t = (get() as unknown as AICFSlice)._timer;
    if (t) clearTimeout(t);
    set({
      jobs: {},
      results: {},
      submitting: false,
      lastList: [],
      nextCursor: undefined,
      error: undefined,
      _reqGen: (get() as unknown as AICFSlice)._reqGen + 1,
      _timer: undefined,
      _intervalMs: 1200,
    } as Partial<StoreState>);
  },

  async enqueueAI(spec: AIJobSpec): Promise<string | undefined> {
    const svc = safeSvc('enqueueAIJob');
    if (!svc) {
      set({ error: 'AICF: enqueueAIJob service not available' } as Partial<StoreState>);
      return undefined;
    }
    set({ submitting: true, error: undefined } as Partial<StoreState>);
    try {
      const res: any = await (svc as any)(spec);
      const jobId: string | undefined = res?.jobId ?? res?.id ?? res;
      if (!jobId) throw new Error('Service did not return jobId');

      const job: JobRecord = {
        id: jobId,
        kind: 'ai',
        status: 'queued',
        createdAt: new Date().toISOString(),
      };

      set((s: any) => ({
        submitting: false,
        jobs: { ...s.jobs, [jobId]: job },
        error: undefined,
      }));

      (get() as unknown as AICFSlice).startPolling();
      return jobId;
    } catch (e: any) {
      set({ submitting: false, error: `AICF enqueueAI failed: ${String(e?.message ?? e)}` } as Partial<StoreState>);
      return undefined;
    }
  },

  async enqueueQuantum(spec: QuantumJobSpec): Promise<string | undefined> {
    const svc = safeSvc('enqueueQuantumJob');
    if (!svc) {
      set({ error: 'AICF: enqueueQuantumJob service not available' } as Partial<StoreState>);
      return undefined;
    }
    set({ submitting: true, error: undefined } as Partial<StoreState>);
    try {
      const res: any = await (svc as any)(spec);
      const jobId: string | undefined = res?.jobId ?? res?.id ?? res;
      if (!jobId) throw new Error('Service did not return jobId');

      const job: JobRecord = {
        id: jobId,
        kind: 'quantum',
        status: 'queued',
        createdAt: new Date().toISOString(),
      };

      set((s: any) => ({
        submitting: false,
        jobs: { ...s.jobs, [jobId]: job },
        error: undefined,
      }));

      (get() as unknown as AICFSlice).startPolling();
      return jobId;
    } catch (e: any) {
      set({ submitting: false, error: `AICF enqueueQuantum failed: ${String(e?.message ?? e)}` } as Partial<StoreState>);
      return undefined;
    }
  },

  async refresh(jobId: string): Promise<JobRecord | undefined> {
    const svc = safeSvc('getJob');
    if (!svc) return undefined;
    try {
      const j: any = await (svc as any)(jobId);
      const next: Partial<JobRecord> = {
        id: j?.id ?? jobId,
        kind: ((j?.kind ?? '').toString().toLowerCase() as JobKind) || (get() as any).jobs[jobId]?.kind || 'ai',
        status: ((j?.status ?? '').toString().toLowerCase() as JobStatus) || 'unknown',
        createdAt: j?.createdAt,
        updatedAt: j?.updatedAt,
        requester: j?.requester,
        providerId: j?.providerId,
        leaseExpiresAt: j?.leaseExpiresAt,
        resultId: j?.resultId,
        cost: j?.cost,
        error: j?.error,
        meta: j?.meta,
      };
      set((s: any) => ({
        jobs: { ...s.jobs, [jobId]: mergeJob(s.jobs[jobId], next) },
      }));

      // If job completed and has a result, fetch it once.
      const final = (get() as any).jobs[jobId] as JobRecord;
      if (final?.resultId && terminal(final.status) && !(get() as any).results[final.resultId]) {
        await (get() as unknown as AICFSlice).fetchResult(final.resultId);
      }
      return (get() as any).jobs[jobId];
    } catch (e) {
      // Soft-fail: keep old state but attach error
      set((s: any) => ({
        jobs: {
          ...s.jobs,
          [jobId]: mergeJob(s.jobs[jobId], {
            error: `refresh failed: ${String((e as any)?.message ?? e)}`,
          }),
        },
      }));
      return (get() as any).jobs[jobId];
    }
  },

  async fetchResult(resultId: string): Promise<ResultRecord | undefined> {
    const svc = safeSvc('getResult');
    if (!svc) return undefined;
    try {
      const r: any = await (svc as any)(resultId);
      const rec: ResultRecord = {
        id: r?.id ?? resultId,
        kind: ((r?.kind ?? '').toString().toLowerCase() as JobKind) || 'ai',
        taskId: r?.taskId,
        outputDigest: r?.outputDigest ?? r?.digest,
        data: r?.data ?? r?.output,
        metrics: r?.metrics,
        verified: r?.verified ?? r?.ok,
        completedAt: r?.completedAt ?? r?.timestamp,
      };
      set((s: any) => ({ results: { ...s.results, [rec.id]: rec } }));
      return rec;
    } catch (e) {
      set({ error: `getResult failed: ${String((e as any)?.message ?? e)}` } as Partial<StoreState>);
      return undefined;
    }
  },

  async list(params?: { cursor?: string; limit?: number; requester?: string }): Promise<string[]> {
    const svc = safeSvc('listJobs');
    if (!svc) {
      set({ error: 'AICF: listJobs service not available' } as Partial<StoreState>);
      return [];
    }
    try {
      const res: any = await (svc as any)(params ?? {});
      const items: any[] = res?.items ?? res ?? [];
      const nextCursor: string | undefined = res?.next ?? res?.cursor ?? undefined;

      set((s: any) => {
        const jobs = { ...s.jobs };
        const ids: string[] = [];
        for (const j of items) {
          const id = j?.id;
          if (!id) continue;
          ids.push(id);
          jobs[id] = mergeJob(jobs[id], {
            id,
            kind: ((j?.kind ?? '').toString().toLowerCase() as JobKind) || 'ai',
            status: ((j?.status ?? '').toString().toLowerCase() as JobStatus) || 'unknown',
            createdAt: j?.createdAt,
            updatedAt: j?.updatedAt,
            requester: j?.requester,
            providerId: j?.providerId,
            leaseExpiresAt: j?.leaseExpiresAt,
            resultId: j?.resultId,
            cost: j?.cost,
            error: j?.error,
            meta: j?.meta,
          })!;
        }
        return { jobs, lastList: ids, nextCursor } as Partial<StoreState>;
      });

      return (get() as any).lastList ?? [];
    } catch (e) {
      set({ error: `listJobs failed: ${String((e as any)?.message ?? e)}` } as Partial<StoreState>);
      return [];
    }
  },

  startPolling() {
    const myGen = (get() as unknown as AICFSlice)._reqGen + 1;
    set({ _reqGen: myGen } as Partial<StoreState>);

    const tick = async () => {
      const state = (get() as unknown as AICFSlice);
      if (state._reqGen !== myGen) return; // canceled / reset
      try {
        const activeIds = Object.values(state.jobs)
          .filter((j) => j && !terminal(j.status))
          .map((j) => j.id);

        if (activeIds.length === 0) {
          // nothing to poll; stop
          const t = state._timer;
          if (t) clearTimeout(t);
          set({ _timer: undefined } as Partial<StoreState>);
          return;
        }

        // refresh in small batches to avoid flooding
        const batch = activeIds.slice(0, 5);
        await Promise.allSettled(batch.map((id) => (get() as unknown as AICFSlice).refresh(id)));

        // schedule next tick with capped backoff (up to 5s)
        const next = Math.min(((get() as unknown as AICFSlice)._intervalMs || 1200) * 1.3, 5000);
        set({ _intervalMs: next } as Partial<StoreState>);
        const t = setTimeout(tick, next);
        set({ _timer: t } as Partial<StoreState>);
      } catch {
        // On unexpected error, try again later
        const t = setTimeout(tick, 2000);
        set({ _timer: t } as Partial<StoreState>);
      }
    };

    // clear previous timer and kick
    const prev = (get() as unknown as AICFSlice)._timer;
    if (prev) clearTimeout(prev);
    set({ _intervalMs: 1200, _timer: undefined } as Partial<StoreState>);
    const t = setTimeout(tick, 80);
    set({ _timer: t } as Partial<StoreState>);
  },

  stopPolling() {
    const t = (get() as unknown as AICFSlice)._timer;
    if (t) clearTimeout(t);
    set({ _timer: undefined } as Partial<StoreState>);
  },
});

registerSlice<AICFSlice>(createAICFSlice);

export default undefined;
