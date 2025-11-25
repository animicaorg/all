import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { sha256Hex } from "../utils/hash";
import type { Abi } from "../types"; // safe to import if your types export Abi; otherwise it stays unused
import { compileSource, linkManifest } from "../services/wasm";

/**
 * useCompile — React hook to compile contract sources to IR, returning
 * diagnostics and a static gas upper bound (when available).
 *
 * This wraps the studio-wasm compiler API exposed via ../services/wasm.
 *
 * Features:
 *  - Debounced auto-compile when inputs change
 *  - Cancellation / latest-call-wins semantics
 *  - Stable input hashing to skip identical compiles
 *  - Maps diagnostics into a normalized shape suitable for editors
 */

export type Severity = "error" | "warning" | "info";

export interface Diagnostic {
  severity: Severity;
  message: string;
  file?: string;
  /** 1-based positions if available from the compiler; fallback to 1 */
  line?: number;
  column?: number;
  endLine?: number;
  endColumn?: number;
  code?: string | number;
}

export interface CompileInputs {
  /**
   * In-memory project tree. Keys are normalized POSIX-style paths
   * (e.g. "contracts/counter/contract.py" or "manifest.json").
   */
  files: Record<string, string>;
  /**
   * Optional manifest object (already parsed). If absent, the compiler may
   * look for "manifest.json" in files.
   */
  manifest?: any;
  /**
   * Optional entry module path (within files). If omitted, the compiler/manifest
   * decides the entrypoint.
   */
  entry?: string;
  /** Toggle optimizations if supported by the compiler */
  optimize?: boolean;
}

export interface CompileResult {
  ok: boolean;
  ir?: Uint8Array; // CBOR-encoded IR blob
  gasUpperBound?: number;
  abi?: Abi | unknown;
  diagnostics: Diagnostic[];
  durationMs: number;
  inputHash: string;
}

type Status = "idle" | "compiling" | "success" | "error";

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
    // The compiler may emit 0-based; convert to 1-based if present.
    const l = typeof d?.line === "number" ? d.line : typeof d?.span?.start?.line === "number" ? d.span.start.line : undefined;
    const c = typeof d?.column === "number" ? d.column : typeof d?.span?.start?.column === "number" ? d.span.start.column : undefined;
    const el = typeof d?.endLine === "number" ? d.endLine : typeof d?.span?.end?.line === "number" ? d.span.end.line : undefined;
    const ec = typeof d?.endColumn === "number" ? d.endColumn : typeof d?.span?.end?.column === "number" ? d.span.end.column : undefined;

    // Ensure 1-based fallback
    const to1 = (n: any) => (typeof n === "number" && n > 0 ? n : typeof n === "number" ? n + 1 : undefined);

    return {
      severity: sev,
      message: String(d?.message ?? d?.msg ?? "Unknown compiler message"),
      file: d?.file ?? d?.path ?? d?.source,
      line: to1(l) ?? 1,
      column: to1(c) ?? 1,
      endLine: to1(el),
      endColumn: to1(ec),
      code: d?.code,
    };
  });
}

async function stableInputHash(inputs: CompileInputs): Promise<string> {
  // Hash a canonical JSON of inputs to dedupe work.
  // Note: we sort file keys for stable hashing.
  const filesSorted = Object.keys(inputs.files)
    .sort()
    .reduce((acc, k) => {
      acc[k] = inputs.files[k];
      return acc;
    }, {} as Record<string, string>);
  const payload = {
    files: filesSorted,
    manifest: inputs.manifest ?? null,
    entry: inputs.entry ?? null,
    optimize: !!inputs.optimize,
  };
  return sha256Hex(JSON.stringify(payload));
}

export interface UseCompileOptions {
  /** Auto-run compile when inputs change (default true) */
  auto?: boolean;
  /** Debounce window for auto compilation (default 250ms) */
  debounceMs?: number;
  /** Skip compile if the deduped input hash matches last result (default true) */
  dedupe?: boolean;
}

export interface UseCompile {
  status: Status;
  result: CompileResult | null;
  error: string | null;
  compiling: boolean;
  /** Imperative compile; returns the finished result (and updates state). */
  compile: (inputs: CompileInputs) => Promise<CompileResult>;
  /** Schedule a compile with debouncing; cancels previous pending schedule. */
  schedule: (inputs: CompileInputs) => void;
  /** Cancel the in-flight or scheduled compile, if any. */
  cancel: () => void;
  /** Hash of the last inputs compiled successfully. */
  lastHash: string | null;
}

export function useCompile(
  inputs?: CompileInputs | null,
  opts: UseCompileOptions = {}
): UseCompile {
  const { auto = true, debounceMs = 250, dedupe = true } = opts;

  const [status, setStatus] = useState<Status>("idle");
  const [result, setResult] = useState<CompileResult | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [lastHash, setLastHash] = useState<string | null>(null);

  const callId = useRef(0);
  const inFlightAbort = useRef<AbortController | null>(null);
  const { run: runDebounced, cancel: cancelDebounce } = useMemo(
    () => debounce(doCompile, debounceMs),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [debounceMs]
  );

  const cancel = useCallback(() => {
    cancelDebounce();
    inFlightAbort.current?.abort();
    inFlightAbort.current = null;
    // Don't change status here; we only flip status on next action.
  }, [cancelDebounce]);

  const compile = useCallback(
    async (ci: CompileInputs): Promise<CompileResult> => {
      cancelDebounce();

      const myId = ++callId.current;
      inFlightAbort.current?.abort();
      const abort = new AbortController();
      inFlightAbort.current = abort;

      setStatus("compiling");
      setError(null);

      const started = performance.now();
      try {
        const hash = await stableInputHash(ci);
        if (dedupe && lastHash && hash === lastHash && result) {
          // Short-circuit: identical inputs; return last result.
          setStatus("success");
          return result;
        }

        // Optionally pre-link manifest if provided separately.
        let manifestObj = ci.manifest;
        if (!manifestObj && ci.files["manifest.json"]) {
          try {
            manifestObj = JSON.parse(ci.files["manifest.json"]);
          } catch (e) {
            // Manifest JSON error is a diagnostic rather than a hard failure
            const diag: Diagnostic = {
              severity: "error",
              message: "manifest.json is not valid JSON",
              file: "manifest.json",
              line: 1,
              column: 1,
            };
            const res: CompileResult = {
              ok: false,
              diagnostics: [diag],
              durationMs: Math.max(0, performance.now() - started),
              inputHash: hash,
            };
            setResult(res);
            setStatus("error");
            setError(diag.message);
            setLastHash(hash);
            return res;
          }
        }

        // Give the worker a chance to short-circuit on aborts.
        if (abort.signal.aborted) throw new Error("Compilation cancelled");

        // Link manifest to ensure stdlib and entry resolution (no-op when not needed).
        if (manifestObj) {
          try {
            await linkManifest(manifestObj);
          } catch (e: any) {
            // Non-fatal; treat as diagnostic
            const diag: Diagnostic = {
              severity: "warning",
              message: `Manifest link warning: ${e?.message ?? String(e)}`,
            };
            // Continue compilation; append diag later.
            // We keep this as a soft hint rather than blocking compilation.
          }
        }

        const out = await compileSource({
          files: ci.files,
          manifest: manifestObj ?? null,
          entry: ci.entry ?? null,
          optimize: !!ci.optimize,
          signal: abort.signal,
        });

        // Expected shape from services/wasm.compileSource:
        // {
        //   ok: boolean,
        //   ir?: Uint8Array,
        //   gasUpperBound?: number,
        //   abi?: any,
        //   diagnostics?: any[]
        // }
        const diagnostics = normalizeDiagnostics(out?.diagnostics);
        const finished = performance.now();
        const res: CompileResult = {
          ok: !!out?.ok && diagnostics.every((d) => d.severity !== "error"),
          ir: out?.ir,
          gasUpperBound: typeof out?.gasUpperBound === "number" ? out.gasUpperBound : undefined,
          abi: out?.abi,
          diagnostics,
          durationMs: finished - started,
          inputHash: hash,
        };

        if (callId.current === myId) {
          setResult(res);
          setStatus(res.ok ? "success" : "error");
          setError(res.ok ? null : "Compilation produced errors");
          setLastHash(hash);
          inFlightAbort.current = null;
        }
        return res;
      } catch (e: any) {
        if (abort.signal.aborted) {
          const res: CompileResult = {
            ok: false,
            diagnostics: [
              {
                severity: "info",
                message: "Compilation cancelled",
              },
            ],
            durationMs: Math.max(0, performance.now() - started),
            inputHash: (await stableInputHash(ci)).toString(),
          };
          if (callId.current === myId) {
            setResult(res);
            setStatus("idle");
            setError(null);
            inFlightAbort.current = null;
          }
          return res;
        }
        const msg = e?.message ?? "Compiler threw";
        const res: CompileResult = {
          ok: false,
          diagnostics: [
            {
              severity: "error",
              message: msg,
            },
          ],
          durationMs: Math.max(0, performance.now() - started),
          inputHash: await stableInputHash(ci),
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

  function doCompile(ci: CompileInputs) {
    // Fire and forget; state is updated by compile()
    // eslint-disable-next-line @typescript-eslint/no-floating-promises
    compile(ci);
  }

  const schedule = useCallback(
    (ci: CompileInputs) => {
      runDebounced(ci);
    },
    [runDebounced]
  );

  // Auto-compile when inputs change
  useEffect(() => {
    if (!auto || !inputs) return;
    schedule(inputs);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [auto, schedule, inputs && JSON.stringify(inputs.files), inputs && JSON.stringify(inputs.manifest), inputs?.entry, inputs?.optimize]);

  return {
    status,
    result,
    error,
    compiling: status === "compiling",
    compile,
    schedule,
    cancel,
    lastHash,
  };
}

export default useCompile;

/* ===========================
   Minimal facade expectation:

   ../services/wasm.ts should export:

   export async function compileSource(args: {
     files: Record<string,string>,
     manifest?: any,
     entry?: string | null,
     optimize?: boolean,
     signal?: AbortSignal
   }): Promise<{
     ok: boolean,
     ir?: Uint8Array,
     gasUpperBound?: number,
     abi?: any,
     diagnostics?: any[]
   }>;

   export async function linkManifest(manifest: any): Promise<void>;

   If your services layer uses a namespaced object, adapt the imports above.
   =========================== */
