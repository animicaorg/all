import { useCallback, useMemo, useRef, useState } from "react";
import * as Services from "../services/servicesApi";

/**
 * useVerify — submit contract-source verification and track job status/results.
 *
 * This hook expects ../services/servicesApi to expose:
 *   - submitVerify(req: {
 *       address?: string; txHash?: string;
 *       source: string; manifest: unknown;
 *     }): Promise<{ jobId: string; status: VerifyPhase }>
 *
 *   - getVerifyStatusByAddress(address: string): Promise<VerifyStatus>
 *   - getVerifyStatusByTx(txHash: string): Promise<VerifyStatus>
 *
 * Where VerifyStatus has (minimum) shape:
 *   {
 *     phase: VerifyPhase;               // "queued" | "running" | "matched" | "mismatch" | "error"
 *     jobId?: string;
 *     address?: string;
 *     txHash?: string;
 *     codeHashComputed?: string;        // hash of provided source+manifest
 *     codeHashOnChain?: string;         // hash detected for contract on chain
 *     manifestHash?: string;
 *     diagnostics?: string[];           // compiler/compare notes
 *     error?: string;                   // reason when phase === "error"
 *   }
 */

export type VerifyPhase = "queued" | "running" | "matched" | "mismatch" | "error";
export type UiStatus =
  | "idle"
  | "submitting"
  | "waiting"
  | "matched"
  | "mismatch"
  | "error";

export interface VerifyStatus {
  phase: VerifyPhase;
  jobId?: string;
  address?: string;
  txHash?: string;
  codeHashComputed?: string;
  codeHashOnChain?: string;
  manifestHash?: string;
  diagnostics?: string[];
  error?: string;
  // Additional fields are tolerated
  [k: string]: unknown;
}

export interface SubmitVerifyParams {
  address?: string;
  txHash?: string;
  source: string;
  manifest: unknown;
}

export interface UseVerify {
  uiStatus: UiStatus;
  error: string | null;
  progress: string[];
  jobId: string | null;
  latest: VerifyStatus | null;

  /** Submit a new verification job. Automatically starts tracking. */
  submit: (params: SubmitVerifyParams, pollMs?: number) => Promise<string>;

  /** Manually fetch current status once (by address). */
  fetchByAddress: (address: string) => Promise<VerifyStatus>;
  /** Manually fetch current status once (by tx hash). */
  fetchByTx: (txHash: string) => Promise<VerifyStatus>;

  /** Begin polling by address (clears any prior polling). */
  trackByAddress: (address: string, pollMs?: number) => void;
  /** Begin polling by tx hash (clears any prior polling). */
  trackByTx: (txHash: string, pollMs?: number) => void;

  /** Stop polling. */
  cancel: () => void;

  /** Reset internal state. */
  reset: () => void;
}

function isDone(phase: VerifyPhase): boolean {
  return phase === "matched" || phase === "mismatch" || phase === "error";
}

export function useVerify(): UseVerify {
  const [uiStatus, setUiStatus] = useState<UiStatus>("idle");
  const [error, setError] = useState<string | null>(null);
  const [progress, setProgress] = useState<string[]>([]);
  const [jobId, setJobId] = useState<string | null>(null);
  const [latest, setLatest] = useState<VerifyStatus | null>(null);

  const timerRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const cancel = useCallback(() => {
    if (timerRef.current) {
      clearInterval(timerRef.current);
      timerRef.current = null;
    }
  }, []);

  const push = useCallback((msg: string) => {
    setProgress((p) => [...p, msg]);
  }, []);

  const reset = useCallback(() => {
    cancel();
    setUiStatus("idle");
    setError(null);
    setProgress([]);
    setJobId(null);
    setLatest(null);
  }, [cancel]);

  const updateFromStatus = useCallback((st: VerifyStatus) => {
    setLatest(st);
    if (st.jobId) setJobId((prev) => prev ?? st.jobId);
    if (st.phase === "error") {
      setUiStatus("error");
      setError(st.error ?? "Verification failed");
    } else if (st.phase === "matched") {
      setUiStatus("matched");
      setError(null);
    } else if (st.phase === "mismatch") {
      setUiStatus("mismatch");
      setError(null);
    } else {
      setUiStatus("waiting");
    }
  }, []);

  const fetchByAddress = useCallback(async (address: string): Promise<VerifyStatus> => {
    const st = await Services.getVerifyStatusByAddress(address);
    updateFromStatus(st);
    return st;
  }, [updateFromStatus]);

  const fetchByTx = useCallback(async (txHash: string): Promise<VerifyStatus> => {
    const st = await Services.getVerifyStatusByTx(txHash);
    updateFromStatus(st);
    return st;
  }, [updateFromStatus]);

  const startPolling = useCallback(
    (fn: () => Promise<VerifyStatus>, pollMs: number) => {
      cancel();
      setUiStatus("waiting");
      // Immediate tick
      fn().catch((e) => {
        // Non-fatal: keep polling unless it's a hard error from API
        push(`Fetch error: ${e?.message ?? e}`);
      });
      timerRef.current = setInterval(async () => {
        try {
          const st = await fn();
          if (isDone(st.phase)) {
            cancel();
          }
        } catch (e: any) {
          // Keep polling, but surface last error to UI
          setError(e?.message ?? "Verify polling failed");
        }
      }, Math.max(500, pollMs));
    },
    [cancel, push]
  );

  const trackByAddress = useCallback(
    (address: string, pollMs = 1500) => {
      push(`Tracking verification by address: ${address}`);
      startPolling(() => Services.getVerifyStatusByAddress(address), pollMs);
    },
    [startPolling, push]
  );

  const trackByTx = useCallback(
    (txHash: string, pollMs = 1500) => {
      push(`Tracking verification by tx: ${txHash}`);
      startPolling(() => Services.getVerifyStatusByTx(txHash), pollMs);
    },
    [startPolling, push]
  );

  const submit = useCallback(
    async (params: SubmitVerifyParams, pollMs = 1500): Promise<string> => {
      setUiStatus("submitting");
      setError(null);
      setProgress([]);
      setJobId(null);
      setLatest(null);

      try {
        if (!params.address && !params.txHash) {
          throw new Error("Provide either address or txHash to verify against.");
        }
        push("Submitting verification job…");
        const res = await Services.submitVerify({
          address: params.address,
          txHash: params.txHash,
          source: params.source,
          manifest: params.manifest,
        });

        const id = res.jobId;
        if (!id) throw new Error("Verification submission did not return a jobId.");
        setJobId(id);
        push(`Job queued: ${id}`);

        // Begin tracking immediately
        if (params.address) {
          trackByAddress(params.address, pollMs);
        } else if (params.txHash) {
          trackByTx(params.txHash, pollMs);
        } else {
          // Should not happen due to earlier checks; fallback to waiting state
          setUiStatus("waiting");
        }

        return id;
      } catch (e: any) {
        const msg = e?.message ?? "Failed to submit verification job";
        setUiStatus("error");
        setError(msg);
        push(msg);
        throw new Error(msg);
      }
    },
    [push, trackByAddress, trackByTx]
  );

  const isActive = useMemo(() => uiStatus === "waiting" || uiStatus === "submitting", [uiStatus]);

  return {
    uiStatus,
    error,
    progress,
    jobId,
    latest,
    submit,
    fetchByAddress,
    fetchByTx,
    trackByAddress,
    trackByTx,
    cancel,
    reset,
  };
}

export default useVerify;
