import { useCallback, useMemo, useRef, useState } from "react";
import * as AICF from "../services/aicf";

/**
 * useAICF — enqueue and track AI/Quantum jobs via studio-services or node RPC adapters.
 *
 * Expected ../services/aicf API (loose-typed; hook is defensive to shape changes):
 *   - enqueueAI(spec: {
 *       model: string; prompt: string; maxUnits?: number; tags?: string[];
 *       feeLimit?: string; // optional human string
 *     }): Promise<{ taskId: string }>
 *
 *   - enqueueQuantum(spec: {
 *       circuit: unknown; shots: number; traps?: number; tags?: string[];
 *       feeLimit?: string;
 *     }): Promise<{ taskId: string }>
 *
 *   - getJob(taskId: string): Promise<JobStatus>
 *   - getResult(taskId: string): Promise<ResultRecord | { notReady: true }>
 */

export type JobKind = "AI" | "Quantum";
export type JobPhase =
  | "queued"
  | "assigned"
  | "running"
  | "completed"
  | "expired"
  | "error";

export interface JobStatus {
  taskId: string;
  kind: JobKind;
  phase: JobPhase;
  createdAt?: string;
  updatedAt?: string;
  providerId?: string;
  units?: number;
  error?: string;
  // passthrough extra fields
  [k: string]: unknown;
}

export interface ResultRecord {
  taskId: string;
  kind: JobKind;
  outputDigest: string;       // hash of canonical output
  payload?: unknown;          // optional opaque output for demos
  height?: number;            // chain height where result became consumable
  proofRefs?: unknown;        // references to on-chain proofs/receipts
  // passthrough
  [k: string]: unknown;
}

export interface UseAICF {
  /** Job map keyed by taskId (includes latest status and optional result). */
  jobs: Record<string, JobStatus & { result?: ResultRecord | null }>;
  /** Most recent non-fatal message and fatal error (if any). */
  lastMessage: string | null;
  lastError: string | null;

  /** Enqueue a new AI job; starts polling unless autoTrack=false. */
  submitAI: (
    spec: Parameters<typeof AICF.enqueueAI>[0],
    opts?: { pollMs?: number; autoTrack?: boolean }
  ) => Promise<string>;

  /** Enqueue a Quantum job; starts polling unless autoTrack=false. */
  submitQuantum: (
    spec: Parameters<typeof AICF.enqueueQuantum>[0],
    opts?: { pollMs?: number; autoTrack?: boolean }
  ) => Promise<string>;

  /** Start/stop tracking specific taskIds. */
  track: (taskId: string, pollMs?: number) => void;
  trackMany: (taskIds: string[], pollMs?: number) => void;
  cancel: (taskId?: string) => void;

  /** One-shot fetch helpers (do not start polling). */
  fetchOnce: (taskId: string) => Promise<JobStatus>;
  fetchResultOnce: (taskId: string) => Promise<ResultRecord | null>;

  /**
   * Wait until job completes (completed/expired/error). Resolves with
   * {status, result} when completed, or throws on expired/error/timeout.
   */
  waitForCompletion: (
    taskId: string,
    opts?: { pollMs?: number; timeoutMs?: number }
  ) => Promise<{ status: JobStatus; result: ResultRecord | null }>;

  /** Clear all state and timers. */
  reset: () => void;
}

const TERMINAL: JobPhase[] = ["completed", "expired", "error"];

export function useAICF(): UseAICF {
  const [jobs, setJobs] = useState<Record<string, JobStatus & { result?: ResultRecord | null }>>({});
  const [lastMessage, setLastMessage] = useState<string | null>(null);
  const [lastError, setLastError] = useState<string | null>(null);

  // Per-task interval timers
  const timersRef = useRef<Map<string, ReturnType<typeof setInterval>>>(new Map());

  const upsertJob = useCallback((st: JobStatus | (JobStatus & { result?: ResultRecord | null })) => {
    setJobs((prev) => {
      const existing = prev[st.taskId] ?? {};
      return {
        ...prev,
        [st.taskId]: { ...(existing as any), ...st },
      };
    });
  }, []);

  const setJobResult = useCallback((taskId: string, result: ResultRecord | null) => {
    setJobs((prev) => {
      const existing = prev[taskId];
      if (!existing) return prev;
      return { ...prev, [taskId]: { ...existing, result } };
    });
  }, []);

  const stopTimer = useCallback((taskId: string) => {
    const t = timersRef.current.get(taskId);
    if (t) clearInterval(t);
    timersRef.current.delete(taskId);
  }, []);

  const cancel = useCallback((taskId?: string) => {
    if (taskId) {
      stopTimer(taskId);
    } else {
      for (const id of timersRef.current.keys()) stopTimer(id);
    }
  }, [stopTimer]);

  const reset = useCallback(() => {
    cancel();
    setJobs({});
    setLastMessage(null);
    setLastError(null);
  }, [cancel]);

  const fetchOnce = useCallback(async (taskId: string): Promise<JobStatus> => {
    const st = (await AICF.getJob(taskId)) as JobStatus;
    upsertJob(st);
    return st;
  }, [upsertJob]);

  const fetchResultOnce = useCallback(async (taskId: string): Promise<ResultRecord | null> => {
    const res = (await AICF.getResult(taskId)) as any;
    if (res && !("notReady" in res)) {
      setJobResult(taskId, res as ResultRecord);
      return res as ResultRecord;
    }
    return null;
  }, [setJobResult]);

  const startPolling = useCallback(
    (taskId: string, pollMs = 1200) => {
      // Clear existing
      cancel(taskId);

      // Immediate tick:
      (async () => {
        try {
          const st = await fetchOnce(taskId);
          if (TERMINAL.includes(st.phase)) {
            if (st.phase === "completed") {
              await fetchResultOnce(taskId).catch(() => void 0);
            }
            return; // Do not start interval if terminal on first tick
          }
        } catch (e: any) {
          setLastError(e?.message ?? String(e));
        }
      })();

      const handle = setInterval(async () => {
        try {
          const st = await fetchOnce(taskId);
          if (TERMINAL.includes(st.phase)) {
            if (st.phase === "completed") {
              await fetchResultOnce(taskId).catch(() => void 0);
            }
            stopTimer(taskId);
          }
        } catch (e: any) {
          // Keep polling but surface the error
          setLastError(e?.message ?? String(e));
        }
      }, Math.max(500, pollMs));

      timersRef.current.set(taskId, handle);
    },
    [cancel, fetchOnce, fetchResultOnce, stopTimer]
  );

  const track = useCallback((taskId: string, pollMs?: number) => {
    setLastMessage(`Tracking job ${taskId}`);
    startPolling(taskId, pollMs);
  }, [startPolling]);

  const trackMany = useCallback((taskIds: string[], pollMs?: number) => {
    for (const id of taskIds) startPolling(id, pollMs);
    setLastMessage(`Tracking ${taskIds.length} job(s)`);
  }, [startPolling]);

  const submitAI = useCallback(
    async (
      spec: Parameters<typeof AICF.enqueueAI>[0],
      opts?: { pollMs?: number; autoTrack?: boolean }
    ): Promise<string> => {
      setLastError(null);
      setLastMessage("Enqueueing AI job…");
      const { taskId } = await AICF.enqueueAI(spec);
      upsertJob({ taskId, kind: "AI", phase: "queued" });
      if (opts?.autoTrack !== false) startPolling(taskId, opts?.pollMs);
      return taskId;
    },
    [upsertJob, startPolling]
  );

  const submitQuantum = useCallback(
    async (
      spec: Parameters<typeof AICF.enqueueQuantum>[0],
      opts?: { pollMs?: number; autoTrack?: boolean }
    ): Promise<string> => {
      setLastError(null);
      setLastMessage("Enqueueing Quantum job…");
      const { taskId } = await AICF.enqueueQuantum(spec);
      upsertJob({ taskId, kind: "Quantum", phase: "queued" });
      if (opts?.autoTrack !== false) startPolling(taskId, opts?.pollMs);
      return taskId;
    },
    [upsertJob, startPolling]
  );

  const waitForCompletion = useCallback(
    async (
      taskId: string,
      opts?: { pollMs?: number; timeoutMs?: number }
    ): Promise<{ status: JobStatus; result: ResultRecord | null }> => {
      const pollMs = Math.max(400, opts?.pollMs ?? 1000);
      const timeoutMs = opts?.timeoutMs ?? 5 * 60_000;

      let resolved = false;
      let timer: any;
      let stopper: any;

      const check = async (): Promise<{ status: JobStatus; result: ResultRecord | null } | null> => {
        const st = await fetchOnce(taskId);
        if (TERMINAL.includes(st.phase)) {
          let res: ResultRecord | null = null;
          if (st.phase === "completed") {
            res = await fetchResultOnce(taskId);
          }
          return { status: st, result: res };
        }
        return null;
      };

      try {
        const first = await check();
        if (first) return first;

        const outcome = await new Promise<{ status: JobStatus; result: ResultRecord | null }>((resolve, reject) => {
          timer = setInterval(async () => {
            try {
              const r = await check();
              if (r) {
                resolved = true;
                clearInterval(timer);
                if (stopper) clearTimeout(stopper);
                resolve(r);
              }
            } catch (e) {
              resolved = true;
              clearInterval(timer);
              if (stopper) clearTimeout(stopper);
              reject(e);
            }
          }, pollMs);

          stopper = setTimeout(() => {
            if (!resolved) {
              clearInterval(timer);
              reject(new Error(`Timed out waiting for job ${taskId} completion`));
            }
          }, timeoutMs);
        });

        return outcome;
      } finally {
        if (!resolved) {
          clearInterval(timer);
        }
      }
    },
    [fetchOnce, fetchResultOnce]
  );

  const hasActive = useMemo(
    () => Array.from(timersRef.current.values()).length > 0,
    [jobs] // touch when jobs change so callers can read updated flags
  );

  // Expose API
  return {
    jobs,
    lastMessage,
    lastError,
    submitAI,
    submitQuantum,
    track,
    trackMany,
    cancel,
    fetchOnce,
    fetchResultOnce,
    waitForCompletion,
    reset,
  };
}

export default useAICF;
