/* eslint-disable no-restricted-globals */
/**
 * PyVM Worker
 * -----------
 * Dedicated Web Worker that boots Pyodide, mounts the bundled Python packages,
 * and routes typed requests to Python (bridge.entry) or arbitrary callables.
 *
 * Protocol (request → response):
 *   Request:
 *     { id: string|number, type: "init", payload: InitPayload }
 *     { id: string|number, type: "install", payload: InstallPayload }
 *     { id: string|number, type: "call", payload: CallPayload }
 *     { id: string|number, type: "runPython", payload: RunPythonPayload }
 *     { id: string|number, type: "version" }
 *
 *   Response:
 *     { id, ok: true, result: any }
 *     { id, ok: false, error: { message: string, name?: string, stack?: string } }
 *
 * You can extend this with higher-level convenience ops in the app layer.
 */

import { initPyVm } from "../pyodide/initPy";
import { ensureMicropip, installPackages } from "../pyodide/packages";
import type { PyodideInstance } from "../pyodide/loader";

// If you have a typed protocol file, you can swap these with imports from
// "../worker/protocol". For now we keep the worker self-contained.

export type WorkerRequest =
  | { id: string | number; type: "init"; payload: InitPayload }
  | { id: string | number; type: "install"; payload: InstallPayload }
  | { id: string | number; type: "call"; payload: CallPayload }
  | { id: string | number; type: "runPython"; payload: RunPythonPayload }
  | { id: string | number; type: "version" };

export type WorkerResponse =
  | { id: string | number; ok: true; result: any }
  | { id: string | number; ok: false; error: { message: string; name?: string; stack?: string } };

export interface InitPayload {
  /** Pyodide base URL (directory that contains pyodide.{js,wasm,data}). */
  baseUrl?: string;
  /** Verbose logs during load/mount. */
  verbose?: boolean;
  /**
   * Files to mount into the Py FS under /lib/py (relative keys like "bridge/entry.py").
   * If omitted, initPyVm will try to bundle via import.meta.glob or fetch at runtime.
   */
  files?: Record<string, string>;
  /** Base URL if using runtime-fetch fallback for /py files. */
  fetchBaseUrl?: string;
  /** Extra pip requirements (micropip). Keep small & pure-Python. */
  requirementsText?: string;
  /** Packages (names or wheel URLs) to install via micropip. */
  packages?: string[];
}

export interface InstallPayload {
  packages?: string[];
  requirementsText?: string;
  extraIndexUrls?: string[];
  verbose?: boolean;
  preRelease?: boolean;
  noDeps?: boolean;
  keepGoing?: boolean;
}

export interface CallPayload {
  /** Fully-qualified Python callable, e.g. "bridge.entry.simulate_tx" */
  fqfn: string;
  /** Positional args (JSON-serializable; for bytes, use BytesBox helper below). */
  args?: any[];
  /** Keyword args (JSON-serializable). */
  kwargs?: Record<string, any>;
}

export interface RunPythonPayload {
  /** Arbitrary Python code string executed with runPythonAsync. */
  code: string;
  /** Optional globals to prebind as `payload` in Python. */
  payload?: any;
}

/** Encode raw bytes for transport to worker. */
export type BytesBox = { __bytes_b64: string };

/** Helper to wrap ArrayBuffer/Uint8Array to BytesBox. */
export function asBytesBox(bytes: ArrayBufferView | ArrayBuffer): BytesBox {
  const u8 = bytes instanceof ArrayBuffer ? new Uint8Array(bytes) : new Uint8Array(bytes.buffer, bytes.byteOffset, bytes.byteLength);
  let s = "";
  for (let i = 0; i < u8.length; i++) s += String.fromCharCode(u8[i]);
  // btoa handles binary-to-base64 in browsers
  return { __bytes_b64: btoa(s) };
}

// ---------------------------------------------------------------------------
// Worker state
// ---------------------------------------------------------------------------

let pyodide: PyodideInstance | null = null;
let initting = false;

// Type the worker scope
declare const self: DedicatedWorkerGlobalScope;

// ---------------------------------------------------------------------------
// Boot & helpers
// ---------------------------------------------------------------------------

/** Load Pyodide via our loader module (supporting a few export variants). */
async function loadPyodideViaLoader(baseUrl?: string): Promise<PyodideInstance> {
  // Dynamic import to avoid bundler cycles in some setups
  const mod = await import("../pyodide/loader");

  // Try commonly-exported entrypoints
  const candidates: Array<keyof typeof mod> = [
    "loadPyodideOnce",
    "loadPyodide",
    "default",
  ];

  let fn: any = null;
  for (const k of candidates) {
    if (k in mod && typeof (mod as any)[k] === "function") {
      fn = (mod as any)[k];
      break;
    }
  }

  if (!fn) {
    throw new Error("No load function exported by ../pyodide/loader. Expected loadPyodideOnce/loadPyodide/default.");
  }

  const instance: PyodideInstance = await fn({ indexURL: baseUrl });
  // Some loaders set globalThis.pyodide; ensure this for initPyVm's fallback path.
  (globalThis as any).pyodide = instance;
  return instance;
}

async function ensureReady(baseUrl?: string): Promise<PyodideInstance> {
  if (pyodide) return pyodide;
  if (initting) {
    // Simple spin-wait; in practice, host should sequence init requests.
    while (initting) await new Promise((r) => setTimeout(r, 10));
    if (pyodide) return pyodide;
  }
  initting = true;
  try {
    pyodide = await loadPyodideViaLoader(baseUrl);
    return pyodide;
  } finally {
    initting = false;
  }
}

function decodeBytesBox(box: BytesBox): Uint8Array {
  const s = atob(box.__bytes_b64);
  const u8 = new Uint8Array(s.length);
  for (let i = 0; i < s.length; i++) u8[i] = s.charCodeAt(i);
  return u8;
}

/** Convert a JS value to a Python object (PyProxy) as best-effort. */
function toPy(py: PyodideInstance, v: any): any {
  if (v && typeof v === "object" && "__bytes_b64" in v) {
    return py.toPy(decodeBytesBox(v as BytesBox));
  }
  // Pyodide will map plain JS objects/arrays/strings/numbers/bools cleanly.
  return py.toPy(v as any);
}

/** Deep-convert a Py result to a JSON-safe JS value (bytes→BytesBox). */
function fromPyResult(v: any, seen = new Set<any>()): any {
  if (v == null) return v;

  // PyProxy → toJs (shallow) then recurse
  if (typeof v === "object" && typeof (v as any).toJs === "function") {
    const js = (v as any).toJs({ dict_converter: Object.fromEntries });
    try {
      (v as any).destroy?.();
    } catch {}
    return fromPyResult(js, seen);
  }

  // Typed arrays or ArrayBuffers → BytesBox
  if (v instanceof Uint8Array || v instanceof ArrayBuffer) {
    const u8 = v instanceof ArrayBuffer ? new Uint8Array(v) : v;
    return asBytesBox(u8);
  }

  if (ArrayBuffer.isView(v)) {
    return asBytesBox(v as ArrayBufferView);
  }

  if (Array.isArray(v)) {
    if (seen.has(v)) return null;
    seen.add(v);
    return v.map((x) => fromPyResult(x, seen));
  }

  if (typeof v === "object") {
    if (seen.has(v)) return null;
    seen.add(v);
    const out: Record<string, any> = {};
    for (const [k, val] of Object.entries(v)) {
      out[k] = fromPyResult(val, seen);
    }
    return out;
  }

  // primitives
  return v;
}

/** Resolve a Python callable object from an fqfn like "a.b.c". */
function resolveCallable(py: PyodideInstance, fqfn: string): any {
  const parts = fqfn.split(".");
  if (parts.length === 0) throw new Error("Empty callable name");
  // Import the base module
  const baseMod = py.pyimport(parts[0]);
  let obj: any = baseMod;
  for (let i = 1; i < parts.length; i++) {
    obj = obj[parts[i]];
  }
  if (typeof obj !== "function" && typeof obj.call !== "function") {
    // PyProxy functions have typeof === "function" in recent Pyodide.
    // Otherwise, check that it is callable by attempting attribute access.
    // We leave a descriptive error:
    throw new Error(`Target "${fqfn}" is not a callable Python object`);
  }
  return obj;
}

// ---------------------------------------------------------------------------
// Router
// ---------------------------------------------------------------------------

self.onmessage = async (evt: MessageEvent<WorkerRequest>) => {
  const msg = evt.data;
  if (!msg || typeof msg !== "object" || !("type" in msg)) return;

  try {
    switch (msg.type) {
      case "init": {
        const payload = msg.payload;
        const py = await ensureReady(payload?.baseUrl);
        // Mount bundled Python packages (vm_pkg + bridge)
        await initPyVm({
          files: payload?.files,
          fetchBaseUrl: payload?.fetchBaseUrl,
          preimport: ["bridge.entry"],
          verbose: !!payload?.verbose,
        });

        // Optional: install extras via micropip
        if (payload?.requirementsText || (payload?.packages && payload.packages.length)) {
          await ensureMicropip(py, !!payload?.verbose);
          if (payload?.requirementsText) {
            // Parse & install requirements
            const { parseRequirementsText } = await import("../pyodide/packages");
            const parsed = parseRequirementsText(payload.requirementsText);
            if (parsed.entries.length) {
              await installPackages(py, parsed.entries, {
                verbose: !!payload?.verbose,
              });
            }
          }
          if (payload?.packages?.length) {
            await installPackages(py, payload.packages, {
              verbose: !!payload?.verbose,
            });
          }
        }

        return ok(msg.id, { ready: true });
      }

      case "install": {
        const p = msg.payload;
        const py = await ensureReady();
        await ensureMicropip(py, !!p?.verbose);
        if (p?.requirementsText) {
          const { parseRequirementsText } = await import("../pyodide/packages");
          const parsed = parseRequirementsText(p.requirementsText);
          if (parsed.entries.length) {
            await installPackages(py, parsed.entries, {
              extraIndexUrls: p?.extraIndexUrls ?? [],
              keepGoing: p?.keepGoing ?? true,
              preRelease: p?.preRelease ?? false,
              noDeps: p?.noDeps ?? false,
              verbose: !!p?.verbose,
            });
          }
        }
        if (p?.packages?.length) {
          await installPackages(py, p.packages, {
            extraIndexUrls: p?.extraIndexUrls ?? [],
            keepGoing: p?.keepGoing ?? true,
            preRelease: p?.preRelease ?? false,
            noDeps: p?.noDeps ?? false,
            verbose: !!p?.verbose,
          });
        }
        return ok(msg.id, { installed: true });
      }

      case "call": {
        const py = await ensureReady();
        const { fqfn, args = [], kwargs = {} } = msg.payload;

        // Resolve callable
        const fn = resolveCallable(py, fqfn);

        // Convert args/kwargs to Python
        const pyArgs = args.map((a) => toPy(py, a));
        const pyKw = toPy(py, kwargs);

        // Call; support async defs (awaitable)
        let res: any;
        try {
          res = fn(...pyArgs, pyKw);
        } catch (e: any) {
          // Some Pyodide versions require kwargs passed as named; retry:
          res = fn(...pyArgs, kwargs);
        }

        // If result is a coroutine/awaitable, await it via runPythonAsync glue
        // Heuristic: awaitables in Pyodide often have a toString that includes "coroutine"
        if (res && typeof res.then === "function") {
          // It's already a JS Promise (rare); just await.
          res = await res;
        } else if (res && typeof res === "object") {
          const name = String(res);
          if (/\bcoroutine\b/i.test(name) || /\bawaitable\b/i.test(name)) {
            // Use Python to await: store in globals then await
            py.globals.set("_worker_tmp_res", res);
            res = await py.runPythonAsync(`
import asyncio
await asyncio.ensure_future(_worker_tmp_res)
`);
            try {
              py.globals.delete("_worker_tmp_res");
            } catch {}
          }
        }

        const out = fromPyResult(res);
        return ok(msg.id, out);
      }

      case "runPython": {
        const py = await ensureReady();
        const { code, payload } = msg.payload;
        if (typeof payload !== "undefined") {
          py.globals.set("payload", py.toPy(payload));
        }
        const result = await py.runPythonAsync(code);
        // Clean up bound globals
        try {
          py.globals.delete("payload");
        } catch {}
        return ok(msg.id, fromPyResult(result));
      }

      case "version": {
        const py = await ensureReady();
        // Ask the bridge for a version banner if available; else Python version.
        let result: any = null;
        try {
          const be = py.pyimport("bridge.entry");
          result = be.version?.() ?? null;
          result = fromPyResult(result);
        } catch {
          // Fallback to Python version string
          result = await py.runPythonAsync(
            "import sys; sys.version.replace('\\n',' ') + ' | ' + sys.executable"
          );
        }
        return ok(msg.id, result);
      }

      default:
        throw new Error(`Unknown worker request type: ${(msg as any).type}`);
    }
  } catch (err: any) {
    return fail(msg.id, normalizeError(err));
  }
};

// ---------------------------------------------------------------------------
// Reply helpers
// ---------------------------------------------------------------------------

function ok(id: string | number, result: any) {
  const resp: WorkerResponse = { id, ok: true, result };
  self.postMessage(resp);
}

function fail(id: string | number, error: { name?: string; message: string; stack?: string }) {
  const resp: WorkerResponse = { id, ok: false, error };
  self.postMessage(resp);
}

function normalizeError(e: any): { name?: string; message: string; stack?: string } {
  if (!e) return { message: "Unknown error" };
  if (typeof e === "string") return { message: e };
  const name = e.name || "Error";
  const message = (e.message || String(e)).replace(/PythonError:\s*/g, "").trim();
  const stack = e.stack;
  return { name, message, stack };
}

export {};
