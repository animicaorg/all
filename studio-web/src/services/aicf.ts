/**
 * AICF (AI/Quantum) job helpers for Studio Web.
 *
 * This module provides:
 *   - enqueueAI / enqueueQuantum: submit dev/test jobs to the AICF queue (if the node exposes it)
 *   - getJob / listJobs: inspect queue state
 *   - getResult: fetch the normalized result record when a job is completed
 *   - pollJob / pollResult: convenience polling with abort/timeout
 *
 * It talks to the node JSON-RPC at {VITE_RPC_URL}/rpc.
 * Read-only calls also consult the capabilities RPC namespace (cap.*) for results.
 *
 * NOTE: On some networks enqueue endpoints may be disabled. In that case you'll get an AICFError
 * with status 405/501. The UI should surface this as "enqueue disabled on this network".
 */

export type Hex = `0x${string}`;

export type ChainId = number;

export type AIJobSpec = {
  model: string;
  prompt: string;
  maxTokens?: number;
  temperature?: number;
  // Optional budget hints; node/AICF may ignore:
  feeLimit?: string | number | bigint;
  priority?: number;
};

export type QuantumJobSpec = {
  circuit: unknown; // JSON-serializable circuit description
  shots: number;
  trapsRatio?: number;
  // Optional budget hints:
  feeLimit?: string | number | bigint;
  priority?: number;
};

export type EnqueueAIRequest = {
  spec: AIJobSpec;
  baseUrl?: string;
  timeoutMs?: number;
};

export type EnqueueQuantumRequest = {
  spec: QuantumJobSpec;
  baseUrl?: string;
  timeoutMs?: number;
};

/** Server-level identifier (usually == task_id). */
export type JobId = string;

export type JobStatus =
  | 'Queued'
  | 'Assigned'
  | 'Running'
  | 'Completed'
  | 'Failed'
  | 'Expired'
  | 'Cancelled';

export type JobRecord = {
  id: JobId;
  kind: 'AI' | 'QUANTUM';
  status: JobStatus;
  createdAt?: number; // epoch ms
  updatedAt?: number; // epoch ms
  providerId?: string;
  // Optional cost fields:
  aiUnits?: number;
  quantumUnits?: number;
  // Normalized spec echo (redacted if sensitive):
  spec?: Record<string, unknown>;
  // Result pointer (may be task_id or an explicit blob key):
  resultRef?: string;
  // Failure info:
  errorCode?: string;
  errorMessage?: string;
};

export type ResultRecord = {
  taskId: JobId;
  kind: 'AI' | 'QUANTUM';
  // Provider- and pipeline-normalized shape:
  output?: unknown; // e.g., text for AI, measurement statistics for Quantum
  digest?: Hex; // sha3 of canonical output
  // Evidence references (for later on-chain proofs):
  evidence?: {
    tee?: Record<string, unknown>;
    traps?: Record<string, unknown>;
    qos?: Record<string, unknown>;
  };
  // Accounting:
  units?: {
    aiUnits?: number;
    quantumUnits?: number;
  };
  // Metadata
  completedAt?: number; // epoch ms
};

export class AICFError extends Error {
  status: number;
  code?: string;
  retryAfterMs?: number;

  constructor(message: string, status = 0, code?: string, retryAfterMs?: number) {
    super(message);
    this.name = 'AICFError';
    this.status = status;
    this.code = code;
    this.retryAfterMs = retryAfterMs;
  }
}

/* --------------------------------------------------------- */
/* Low-level JSON-RPC helpers                                */
/* --------------------------------------------------------- */

const DEFAULT_TIMEOUT_MS = 20_000;

function rpcBaseUrl(override?: string): string {
  const envUrl = (import.meta as any).env?.VITE_RPC_URL as string | undefined;
  const url = (override ?? envUrl)?.replace(/\/+$/, '');
  if (!url) throw new Error('RPC base URL not configured. Set VITE_RPC_URL or pass baseUrl.');
  return url;
}

let _rpcId = 0;
async function rpcCall<T = any>(
  method: string,
  params?: unknown,
  opts?: { baseUrl?: string; timeoutMs?: number }
): Promise<T> {
  const url = `${rpcBaseUrl(opts?.baseUrl)}/rpc`;
  const id = ++_rpcId;
  const ctrl = new AbortController();
  const t = setTimeout(() => ctrl.abort(), opts?.timeoutMs ?? DEFAULT_TIMEOUT_MS);

  try {
    const res = await fetch(url, {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify({ jsonrpc: '2.0', id, method, params }),
      signal: ctrl.signal,
      credentials: 'omit',
      cache: 'no-store',
    });
    if (!res.ok) {
      let detail: any = null;
      try {
        detail = await res.json();
      } catch {
        /* ignore */
      }
      const code = detail?.error?.code || detail?.code;
      const message =
        detail?.error?.message ||
        detail?.message ||
        (res.status === 404
          ? 'Method not found'
          : res.status === 405
          ? 'Method disabled'
          : `HTTP ${res.status} error`);
      const retryAfter = parseRetryAfter(res.headers.get('retry-after'));
      throw new AICFError(message, res.status, code, retryAfter);
    }
    const body = await res.json();
    if (body.error) {
      const code = body.error.code;
      const message = body.error.message || 'RPC error';
      throw new AICFError(message, 0, String(code));
    }
    return body.result as T;
  } catch (e: any) {
    if (e?.name === 'AbortError') {
      throw new AICFError('Request timed out', 0, 'TIMEOUT');
    }
    if (e instanceof AICFError) throw e;
    throw new AICFError(e?.message || 'Network error', 0, 'NETWORK');
  } finally {
    clearTimeout(t);
  }
}

function parseRetryAfter(h: string | null): number | undefined {
  if (!h) return;
  const n = Number(h);
  if (Number.isFinite(n) && n >= 0) return Math.round(n * 1000);
}

/* --------------------------------------------------------- */
/* Public API                                                */
/* --------------------------------------------------------- */

/** Submit an AI job to the AICF queue (if enabled). */
export async function enqueueAI(req: EnqueueAIRequest): Promise<{ jobId: JobId; record?: JobRecord }> {
  // Preferred method name. If the node uses a different name (legacy), we retry below.
  const primary = async () =>
    rpcCall<{ jobId: JobId; job?: JobRecord }>('aicf.enqueueAI', { spec: req.spec }, req);
  try {
    const out = await primary();
    return { jobId: out.jobId, record: normalizeJob(out.job) };
  } catch (e: any) {
    // Fallback to generic queue submit method if exposed
    if (e instanceof AICFError && (e.status === 404 || e.code === '-32601')) {
      const out = await rpcCall<{ jobId: JobId; job?: JobRecord }>(
        'aicf.queueSubmit',
        { kind: 'AI', spec: req.spec },
        req
      );
      return { jobId: out.jobId, record: normalizeJob(out.job) };
    }
    throw e;
  }
}

/** Submit a Quantum job to the AICF queue (if enabled). */
export async function enqueueQuantum(
  req: EnqueueQuantumRequest
): Promise<{ jobId: JobId; record?: JobRecord }> {
  const primary = async () =>
    rpcCall<{ jobId: JobId; job?: JobRecord }>('aicf.enqueueQuantum', { spec: req.spec }, req);
  try {
    const out = await primary();
    return { jobId: out.jobId, record: normalizeJob(out.job) };
  } catch (e: any) {
    if (e instanceof AICFError && (e.status === 404 || e.code === '-32601')) {
      const out = await rpcCall<{ jobId: JobId; job?: JobRecord }>(
        'aicf.queueSubmit',
        { kind: 'QUANTUM', spec: req.spec },
        req
      );
      return { jobId: out.jobId, record: normalizeJob(out.job) };
    }
    throw e;
  }
}

/** Get a single job by id. */
export async function getJob(jobId: JobId, opts?: { baseUrl?: string; timeoutMs?: number }): Promise<JobRecord> {
  const out = await rpcCall<JobRecord>('aicf.getJob', { id: jobId }, opts);
  return normalizeJob(out);
}

/** List jobs (optionally by status, kind, provider, or requester). */
export async function listJobs(params?: {
  status?: JobStatus;
  kind?: 'AI' | 'QUANTUM';
  providerId?: string;
  requester?: string; // address
  page?: number;
  pageSize?: number;
  baseUrl?: string;
  timeoutMs?: number;
}): Promise<{ items: JobRecord[]; nextPage?: number | null }> {
  const out = await rpcCall<{ items: JobRecord[]; nextPage?: number | null }>(
    'aicf.listJobs',
    {
      status: params?.status,
      kind: params?.kind,
      providerId: params?.providerId,
      requester: params?.requester,
      page: params?.page ?? 1,
      pageSize: params?.pageSize ?? 25,
    },
    params
  );
  return { items: out.items?.map(normalizeJob) ?? [], nextPage: out.nextPage ?? null };
}

/** Fetch result record from capabilities RPC (read-only). */
export async function getResult(taskId: JobId, opts?: { baseUrl?: string; timeoutMs?: number }): Promise<ResultRecord> {
  // cap.getResult expects { task_id } in most deployments
  const out = await rpcCall<ResultRecord>('cap.getResult', { task_id: taskId }, opts);
  return normalizeResult(out);
}

/** Poll a job until status is terminal (Completed/Failed/Expired/Cancelled). */
export async function pollJob(jobId: JobId, opts?: {
  baseUrl?: string;
  intervalMs?: number;
  timeoutMs?: number;
  onUpdate?: (jr: JobRecord) => void;
  signal?: AbortSignal;
}): Promise<JobRecord> {
  const started = Date.now();
  const interval = Math.max(500, opts?.intervalMs ?? 1500);
  const timeout = opts?.timeoutMs ?? 120_000;

  // eslint-disable-next-line no-constant-condition
  while (true) {
    if (opts?.signal?.aborted) throw new AICFError('Polling aborted', 0, 'ABORTED');
    const jr = await getJob(jobId, { baseUrl: opts?.baseUrl, timeoutMs: Math.max(5_000, interval) });
    opts?.onUpdate?.(jr);
    if (isTerminal(jr.status)) return jr;
    if (Date.now() - started > timeout) throw new AICFError('Polling timed out', 0, 'TIMEOUT');
    await sleep(interval, opts?.signal);
  }
}

/** Poll result until available. Optionally short-circuits when job reaches a terminal status. */
export async function pollResult(taskId: JobId, opts?: {
  baseUrl?: string;
  intervalMs?: number;
  timeoutMs?: number;
  onUpdate?: (jr: JobRecord) => void;
  signal?: AbortSignal;
}): Promise<ResultRecord> {
  const started = Date.now();
  const interval = Math.max(500, opts?.intervalMs ?? 1500);
  const timeout = opts?.timeoutMs ?? 120_000;

  // eslint-disable-next-line no-constant-condition
  while (true) {
    if (opts?.signal?.aborted) throw new AICFError('Polling aborted', 0, 'ABORTED');

    // Try fetching the result first (fast path)
    try {
      const rr = await getResult(taskId, { baseUrl: opts?.baseUrl, timeoutMs: Math.max(5_000, interval) });
      return rr;
    } catch (e: any) {
      // If not found, continue; escalate only on non-404/-32602 style errors
      if (!(e instanceof AICFError && (e.status === 404 || e.code === '-32602' || e.code === 'NETWORK'))) {
        // transient network errors also just retry
      }
    }

    // Optionally surface job updates to the UI
    try {
      const jr = await getJob(taskId, { baseUrl: opts?.baseUrl, timeoutMs: Math.max(5_000, interval) });
      opts?.onUpdate?.(jr);
      // If job is terminal and still no result, we keep polling briefly for stragglers
      if (isTerminal(jr.status) && Date.now() - started > Math.min(timeout, 15_000)) {
        throw new AICFError('Job finished without a result record', 0, 'NO_RESULT');
      }
    } catch {
      /* ignore */
    }

    if (Date.now() - started > timeout) throw new AICFError('Polling timed out', 0, 'TIMEOUT');
    await sleep(interval, opts?.signal);
  }
}

/* --------------------------------------------------------- */
/* Helpers                                                   */
/* --------------------------------------------------------- */

function sleep(ms: number, signal?: AbortSignal) {
  return new Promise<void>((resolve, reject) => {
    const t = setTimeout(() => {
      signal?.removeEventListener('abort', onAbort);
      resolve();
    }, ms);
    const onAbort = () => {
      clearTimeout(t);
      reject(new AICFError('Sleep aborted', 0, 'ABORTED'));
    };
    if (signal) signal.addEventListener('abort', onAbort, { once: true });
  });
}

function isTerminal(s: JobStatus): boolean {
  return s === 'Completed' || s === 'Failed' || s === 'Expired' || s === 'Cancelled';
}

function normalizeJob(j?: any): JobRecord | undefined {
  if (!j) return undefined;
  return {
    id: String(j.id ?? j.jobId ?? j.taskId ?? j.task_id ?? ''),
    kind: (j.kind === 'AI' || j.kind === 'QUANTUM') ? j.kind : (j.kind?.toUpperCase?.() ?? 'AI'),
    status: j.status as JobStatus,
    createdAt: j.createdAt ?? j.created_at ?? undefined,
    updatedAt: j.updatedAt ?? j.updated_at ?? undefined,
    providerId: j.providerId ?? j.provider_id ?? undefined,
    aiUnits: j.aiUnits ?? j.units?.ai ?? undefined,
    quantumUnits: j.quantumUnits ?? j.units?.quantum ?? undefined,
    spec: j.spec ?? undefined,
    resultRef: j.resultRef ?? j.result_ref ?? undefined,
    errorCode: j.errorCode ?? j.error_code ?? undefined,
    errorMessage: j.errorMessage ?? j.error_message ?? undefined,
  };
}

function normalizeResult(r: any): ResultRecord {
  return {
    taskId: String(r.taskId ?? r.task_id ?? r.id ?? ''),
    kind: (r.kind === 'AI' || r.kind === 'QUANTUM') ? r.kind : (r.kind?.toUpperCase?.() ?? 'AI'),
    output: r.output ?? r.result ?? undefined,
    digest: r.digest ?? r.resultDigest ?? r.result_digest ?? undefined,
    evidence: r.evidence ?? {
      tee: r.tee ?? undefined,
      traps: r.traps ?? undefined,
      qos: r.qos ?? undefined,
    },
    units: r.units ?? {
      aiUnits: r.aiUnits ?? r.ai_units ?? undefined,
      quantumUnits: r.quantumUnits ?? r.quantum_units ?? undefined,
    },
    completedAt: r.completedAt ?? r.completed_at ?? undefined,
  };
}

/* --------------------------------------------------------- */
/* Optional: Providers API (for dashboards)                  */
/* --------------------------------------------------------- */

export type ProviderRecord = {
  id: string;
  caps: ('AI' | 'QUANTUM')[];
  stake?: string;
  status?: 'Active' | 'Jailed' | 'CoolingDown' | 'Inactive';
  region?: string;
  health?: number; // 0..1
  endpoints?: { api?: string } | Record<string, unknown>;
};

export async function listProviders(opts?: {
  baseUrl?: string;
  timeoutMs?: number;
}): Promise<ProviderRecord[]> {
  const out = await rpcCall<{ items: any[] }>('aicf.listProviders', {}, opts);
  return (out.items ?? []).map((p) => ({
    id: String(p.id),
    caps: Array.isArray(p.caps) ? p.caps : [],
    stake: p.stake,
    status: p.status,
    region: p.region,
    health: p.health,
    endpoints: p.endpoints,
  }));
}

export default {
  enqueueAI,
  enqueueQuantum,
  getJob,
  listJobs,
  getResult,
  pollJob,
  pollResult,
  listProviders,
  AICFError,
};
