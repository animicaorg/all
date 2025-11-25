import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { sha256Hex } from "../utils/hash";
import { toHex } from "../utils/bytes";
import { simulateCall, estimateGas as wasmEstimateGas } from "../services/wasm";

/**
 * useSimulate — React hook to simulate a contract call in the browser using
 * the studio-wasm simulator. Pairs nicely with useCompile().
 *
 * Features:
 *  - Debounced auto-run when inputs change
 *  - Cancellation and "latest-call-wins" semantics
 *  - Stable input hashing for dedupe
 *  - Gas estimation helper
 */

export type Severity = "error" | "warning" | "info";

export interface Diagnostic {
  severity: Severity;
  message: string;
  file?: string;
  line?: number;
  column?: number;
  endLine?: number;
  endColumn?: number;
  code?: string | number;
}

export interface SimulateCallInputs {
  /** Compiled IR blob (CBOR-encoded) produced by the compiler. */
  ir: Uint8Array;
  /** Entrypoint function name to call. */
  fn: string;
  /** Positional arguments in ABI-friendly JSON (numbers/strings/arrays/objects/bytes as hex). */
  args: unknown[];
  /** Optional ephemeral state handle; simulator may return an updated handle. */
  state?: string | null;
  /** Optional caller address (hex or bech32 per chain rules). */
  caller?: string;
  /** Optional token value to transfer (as bigint or decimal string). */
  value?: string | bigint;
  /** Optional explicit gas limit for the simulation. */
  gasLimit?: number;
  /** Optional block env overrrides. */
  block?: { number?: number; timestamp?: number };
}

export interface DecodedEvent {
  name?: string;
  /** Decoded args or a raw shape depending on ABI availability within the simulator. */
  args?: Record<string, unknown> | unknown[];
  /** Raw, if returned (topics, data, etc.). */
  raw?: any;
}

export interface SimulateResult {
  ok: boolean;
  returnValue?: unknown;
  logs?: DecodedEvent[];
  gasUsed?: number;
  /** Next state handle, if the simulator supports persistent ephemeral state. */
  state?: string | null;
  traces?: any[];
  diagnostics: Diagnostic[];
  durationMs: number;
  inputHash: string;
}

type Status = "idle" | "running" | "success" | "error";

export interface UseSimulateOptions {
  /** Auto-run simulation when inputs change (default true). */
  auto?: boolean;
  /** Debounce window for auto runs (default 200ms). */
  debounceMs?: number;
  /** Skip if the deduped input hash matches last successful result (default true). */
  dedupe?: boolean;
}

export interface UseSimulate {
  status: Status;
  running: boolean;
  result: SimulateResult | null;
  error: string | null;
  /** Imperative simulate; returns the finished result and updates state. */
  simulate: (inputs: SimulateCallInputs) => Promise<SimulateResult>;
  /** Estimate gas for given inputs (no state updates). */
  estimateGas: (inputs: Omit<SimulateCallInputs, "gasLimit">) => Promise<number | null>;
  /** Debounced simulate; cancels previous pending schedule. */
  schedule: (inputs: SimulateCallInputs) => void;
  /** Cancel in-flight or scheduled simulation. */
  cancel: () => void;
  /** Hash of the last inputs simulated successfully. */
  lastHash: string | null;
}

/** Debounce helper returning a cancel function. */
function debounce<T extends (...args: any[]) => any>(fn: T, ms: number) {
  let t: any;
  const debounced = (...args: Parameters<T>) => {
    clearTimeout(t);
    t = setTimeout(() => fn(...args), ms);
  };
  const cancel = () => clearTimeout(t);
  return { run: debounced, cancel };
}

function normalizeDiagnostics(raw: any[] | undefined | null): Diagnostic[] {
  if (!raw) return [];
  return raw.map((d) => {
    const sev: Severity =
      d?.severity === "warning" ? "warning" : d?.severity === "info" ? "info" : "error";
    // Map common spans into 1-based editor coordinates.
    const l = typeof d?.line === "number" ? d.line : d?.span?.start?.line;
    const c = typeof d?.column === "number" ? d.column : d?.span?.start?.column;
    const el = typeof d?.endLine === "number" ? d.endLine : d?.span?.end?.line;
    const ec = typeof d?.endColumn === "number" ? d.endColumn : d?.span?.end?.column;

    const to1 = (n: any) => (typeof n === "number" && n > 0 ? n : typeof n === "number" ? n + 1 : undefined);

    return {
      severity: sev,
      message: String(d?.message ?? d?.msg ?? "Unknown simulator message"),
      file: d?.file ?? d?.path ?? d?.source,
      line: to1(l) ?? 1,
      column: to1(c) ?? 1,
      endLine: to1(el),
      endColumn: to1(ec),
      code: d?.code,
    };
  });
}

async function stableInputHash(ci: SimulateCallInputs): Promise<string> {
  // Avoid hashing potentially huge binary by converting to hex string once.
  const irHex = toHex(ci.ir);
  const payload = {
    irHex,
    fn: ci.fn,
    args: ci.args,
    state: ci.state ?? null,
    caller: ci.caller ?? null,
    value: typeof ci.value === "bigint" ? ci.value.toString() : ci.value ?? null,
    gasLimit: ci.gasLimit ?? null,
    block: ci.block ?? null,
  };
  return sha256Hex(JSON.stringify(payload));
}

export function useSimulate(
  inputs?: SimulateCallInputs | null,
  opts: UseSimulateOptions = {}
): UseSimulate {
  const { auto = true, debounceMs = 200, dedupe = true } = opts;

  const [status, setStatus] = useState<Status>("idle");
  const [result, setResult] = useState<SimulateResult | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [lastHash, setLastHash] = useState<string | null>(null);

  const callId = useRef(0);
  const inFlightAbort = useRef<AbortController | null>(null);
  const { run: runDebounced, cancel: cancelDebounce } = useMemo(
    () => debounce(doSimulate, debounceMs),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [debounceMs]
  );

  const cancel = useCallback(() => {
    cancelDebounce();
    inFlightAbort.current?.abort();
    inFlightAbort.current = null;
  }, [cancelDebounce]);

  const simulate = useCallback(
    async (ci: SimulateCallInputs): Promise<SimulateResult> => {
      cancelDebounce();

      const myId = ++callId.current;
      inFlightAbort.current?.abort();
      const abort = new AbortController();
      inFlightAbort.current = abort;

      setStatus("running");
      setError(null);

      const started = performance.now();
      try {
        if (!ci || !(ci.ir instanceof Uint8Array) || ci.ir.length === 0) {
          const diag: Diagnostic = {
            severity: "error",
            message: "No compiled IR provided for simulation.",
          };
          const res: SimulateResult = {
            ok: false,
            diagnostics: [diag],
            durationMs: Math.max(0, performance.now() - started),
            inputHash: "missing-ir",
          };
          setResult(res);
          setStatus("error");
          setError(diag.message);
          return res;
        }

        const hash = await stableInputHash(ci);
        if (dedupe && lastHash && result && hash === lastHash) {
          setStatus(result.ok ? "success" : "error");
          return result;
        }

        if (abort.signal.aborted) throw new Error("Simulation cancelled");

        const out = await simulateCall({
          ir: ci.ir,
          fn: ci.fn,
          args: ci.args ?? [],
          state: ci.state ?? null,
          caller: ci.caller ?? null,
          value: typeof ci.value === "bigint" ? ci.value.toString() : (ci.value as string | null) ?? null,
          gasLimit: ci.gasLimit ?? null,
          block: ci.block ?? null,
          signal: abort.signal,
        });

        // Expected shape from services/wasm.simulateCall():
        // {
        //   ok: boolean,
        //   returnValue?: unknown,
        //   logs?: any[],
        //   gasUsed?: number,
        //   state?: string | null,
        //   traces?: any[],
        //   diagnostics?: any[]
        // }
        const diagnostics = normalizeDiagnostics(out?.diagnostics);
        const finished = performance.now();
        const res: SimulateResult = {
          ok: !!out?.ok && diagnostics.every((d) => d.severity !== "error"),
          returnValue: out?.returnValue,
          logs: out?.logs,
          gasUsed: typeof out?.gasUsed === "number" ? out.gasUsed : undefined,
          state: typeof out?.state === "string" || out?.state === null ? out.state : undefined,
          traces: out?.traces,
          diagnostics,
          durationMs: finished - started,
          inputHash: hash,
        };

        if (callId.current === myId) {
          setResult(res);
          setStatus(res.ok ? "success" : "error");
          setError(res.ok ? null : "Simulation produced errors");
          setLastHash(hash);
          inFlightAbort.current = null;
        }
        return res;
      } catch (e: any) {
        if (abort.signal.aborted) {
          const res: SimulateResult = {
            ok: false,
            diagnostics: [{ severity: "info", message: "Simulation cancelled" }],
            durationMs: Math.max(0, performance.now() - started),
            inputHash: await (async () => (ci ? stableInputHash(ci) : Promise.resolve("cancelled")))(),
          };
          if (callId.current === myId) {
            setResult(res);
            setStatus("idle");
            setError(null);
            inFlightAbort.current = null;
          }
          return res;
        }
        const msg = e?.message ?? "Simulator threw";
        const res: SimulateResult = {
          ok: false,
          diagnostics: [{ severity: "error", message: msg }],
          durationMs: Math.max(0, performance.now() - started),
          inputHash: await (async () => (ci ? stableInputHash(ci) : Promise.resolve("error")))(),
        };
        if (callId.current === myId) {
          setResult(res);
          setStatus("error");
          setError(msg);
          inFlightAbort.current = null;
        }
        return res;
      }
    },
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [dedupe, lastHash, result]
  );

  function doSimulate(ci: SimulateCallInputs) {
    // Fire-and-forget; state gets updated by simulate()
    // eslint-disable-next-line @typescript-eslint/no-floating-promises
    simulate(ci);
  }

  const schedule = useCallback(
    (ci: SimulateCallInputs) => {
      runDebounced(ci);
    },
    [runDebounced]
  );

  const estimateGas = useCallback(
    async (ci: Omit<SimulateCallInputs, "gasLimit">): Promise<number | null> => {
      try {
        if (!ci || !(ci.ir instanceof Uint8Array) || ci.ir.length === 0) return null;
        const n = await wasmEstimateGas({
          ir: ci.ir,
          fn: ci.fn,
          args: ci.args ?? [],
          state: ci.state ?? null,
          caller: ci.caller ?? null,
          value: typeof ci.value === "bigint" ? ci.value.toString() : (ci.value as string | null) ?? null,
        });
        return typeof n === "number" && Number.isFinite(n) ? n : null;
      } catch {
        return null;
      }
    },
    []
  );

  // Auto-run when inputs change
  useEffect(() => {
    if (!auto || !inputs) return;
    // Depend only on serializable fields; IR affects hash through toHex
    const key = JSON.stringify({
      ir: inputs.ir ? toHex(inputs.ir) : "",
      fn: inputs.fn,
      args: inputs.args,
      state: inputs.state ?? null,
      caller: inputs.caller ?? null,
      value: typeof inputs.value === "bigint" ? inputs.value.toString() : inputs.value ?? null,
      gasLimit: inputs.gasLimit ?? null,
      block: inputs.block ?? null,
    });
    schedule(inputs);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [
    auto,
    schedule,
    inputs &&
      JSON.stringify({
        ir: inputs.ir ? toHex(inputs.ir) : "",
        fn: inputs.fn,
        args: inputs.args,
        state: inputs.state ?? null,
        caller: inputs.caller ?? null,
        value: typeof inputs.value === "bigint" ? inputs.value.toString() : inputs.value ?? null,
        gasLimit: inputs.gasLimit ?? null,
        block: inputs.block ?? null,
      }),
  ]);

  return {
    status,
    running: status === "running",
    result,
    error,
    simulate,
    estimateGas,
    schedule,
    cancel,
    lastHash,
  };
}

export default useSimulate;

/* ===========================
   Minimal facade expectation:

   ../services/wasm.ts should export:

   export async function simulateCall(args: {
     ir: Uint8Array,
     fn: string,
     args: unknown[],
     state?: string | null,
     caller?: string | null,
     value?: string | null,
     gasLimit?: number | null,
     block?: { number?: number; timestamp?: number } | null,
     signal?: AbortSignal
   }): Promise<{
     ok: boolean,
     returnValue?: unknown,
     logs?: any[],
     gasUsed?: number,
     state?: string | null,
     traces?: any[],
     diagnostics?: any[]
   }>;

   export async function estimateGas(args: {
     ir: Uint8Array,
     fn: string,
     args: unknown[],
     state?: string | null,
     caller?: string | null,
     value?: string | null
   }): Promise<number>;

   =========================== */
