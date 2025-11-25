/**
 * Worker Protocol (Type-Safe)
 * ===========================
 * Shared request/response contracts and a small client for talking to
 * `pyvm.worker.ts`. Keep this file free of DOM-only types so it can be
 * imported in Node test environments (JS-DOM, Vitest) as well.
 */

export const PROTOCOL_VERSION = 1;

/* ---------------------------------- Bytes --------------------------------- */

export type BytesBox = { __bytes_b64: string };

/** True if the value is a BytesBox wrapper. */
export function isBytesBox(v: unknown): v is BytesBox {
  return !!v && typeof v === "object" && "__bytes_b64" in (v as any);
}

/** Wrap ArrayBuffer/TypedArray as BytesBox for safe structured-clone. */
export function asBytesBox(bytes: ArrayBufferView | ArrayBuffer): BytesBox {
  const u8 =
    bytes instanceof ArrayBuffer
      ? new Uint8Array(bytes)
      : new Uint8Array(bytes.buffer, bytes.byteOffset, bytes.byteLength);
  let s = "";
  for (let i = 0; i < u8.length; i++) s += String.fromCharCode(u8[i]);
  return { __bytes_b64: btoa(s) };
}

/** Decode BytesBox back to Uint8Array. */
export function fromBytesBox(box: BytesBox): Uint8Array {
  const s = atob(box.__bytes_b64);
  const out = new Uint8Array(s.length);
  for (let i = 0; i < s.length; i++) out[i] = s.charCodeAt(i);
  return out;
}

/* --------------------------------- Payloads -------------------------------- */

export interface InitPayload {
  baseUrl?: string; // pyodide base URL (dir with pyodide.{js,wasm,data})
  verbose?: boolean;
  files?: Record<string, string>; // path → file text (mounted under /lib/py)
  fetchBaseUrl?: string; // for runtime fetch fallback of /py assets
  requirementsText?: string; // micropip requirements.txt content
  packages?: string[]; // micropip packages (names or wheel URLs)
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
  fqfn: string; // e.g. "bridge.entry.simulate_tx"
  args?: any[];
  kwargs?: Record<string, any>;
}

export interface RunPythonPayload {
  code: string;
  payload?: any; // bound as "payload" in Python globals before exec
}

/* --------------------------------- Envelopes -------------------------------- */

export type WorkerRequest =
  | { id: string | number; type: "init"; payload: InitPayload }
  | { id: string | number; type: "install"; payload: InstallPayload }
  | { id: string | number; type: "call"; payload: CallPayload }
  | { id: string | number; type: "runPython"; payload: RunPythonPayload }
  | { id: string | number; type: "version" };

export type WorkerResponse =
  | { id: string | number; ok: true; result: any }
  | { id: string | number; ok: false; error: { message: string; name?: string; stack?: string } };

/** Map a request type to the corresponding successful result payload. */
export type ResultOf<T extends WorkerRequest> =
  T["type"] extends "init"
    ? { ready: true }
    : T["type"] extends "install"
    ? { installed: true }
    : T["type"] extends "version"
    ? any
    : T["type"] extends "call"
    ? any
    : T["type"] extends "runPython"
    ? any
    : never;

/* ---------------------------------- Client --------------------------------- */

export interface ClientOptions {
  /** Default request timeout (ms). */
  timeoutMs?: number;
  /** Optional AbortSignal used as a global kill-switch for all requests. */
  signal?: AbortSignal;
}

/**
 * Minimal, promise-based client for a DedicatedWorker that implements this
 * protocol. It multiplexes requests via ids and resolves only matching replies.
 */
export class PyVmWorkerClient {
  private worker: Worker;
  private timeoutMs: number;
  private inFlight = new Map<
    string | number,
    {
      resolve: (v: any) => void;
      reject: (e: any) => void;
      timer: any;
    }
  >;
  private nextId = makeIdFactory();
  private disposed = false;

  constructor(worker: Worker, opts: ClientOptions = {}) {
    this.worker = worker;
    this.timeoutMs = opts.timeoutMs ?? 120_000;
    this.inFlight = new Map();

    // wire replies
    this.worker.addEventListener("message", (ev: MessageEvent<WorkerResponse>) => {
      const msg = ev.data;
      if (!msg || typeof msg !== "object" || !("id" in msg)) return;
      const entry = this.inFlight.get(msg.id);
      if (!entry) return; // not ours
      clearTimeout(entry.timer);
      this.inFlight.delete(msg.id);
      if ((msg as any).ok) {
        entry.resolve((msg as any).result);
      } else {
        const err = new Error((msg as any).error?.message || "Worker error");
        (err as any).name = (msg as any).error?.name ?? "WorkerError";
        (err as any).stack = (msg as any).error?.stack ?? err.stack;
        entry.reject(err);
      }
    });

    this.worker.addEventListener("error", (ev) => {
      // hard-fail all in-flight promises on worker error
      const err = new Error(`Worker error: ${String((ev as any).message ?? ev)}`);
      for (const [id, entry] of this.inFlight) {
        clearTimeout(entry.timer);
        entry.reject(err);
        this.inFlight.delete(id);
      }
    });

    if (opts.signal) {
      opts.signal.addEventListener("abort", () => this.dispose(new Error("Aborted by signal")));
    }
  }

  /** Gracefully terminates the client; rejects any in-flight requests. */
  dispose(reason?: Error) {
    if (this.disposed) return;
    this.disposed = true;
    const err = reason ?? new Error("Worker client disposed");
    for (const [id, entry] of this.inFlight) {
      clearTimeout(entry.timer);
      entry.reject(err);
      this.inFlight.delete(id);
    }
    try {
      this.worker.terminate();
    } catch {
      // ignore
    }
  }

  /** Low-level send with typed request and typed success response. */
  send<T extends WorkerRequest>(
    req: Omit<T, "id"> & { id?: string | number },
    timeoutMs = this.timeoutMs
  ): Promise<ResultOf<T>> {
    if (this.disposed) return Promise.reject(new Error("Client disposed"));
    const id = req.id ?? this.nextId();
    return new Promise<ResultOf<T>>((resolve, reject) => {
      const timer = setTimeout(() => {
        this.inFlight.delete(id);
        reject(new Error(`Worker request timed out after ${timeoutMs} ms (type=${(req as any).type})`));
      }, timeoutMs);

      this.inFlight.set(id, { resolve, reject, timer });
      const envelope = { ...(req as any), id } as WorkerRequest;
      this.worker.postMessage(envelope);
    });
  }

  /* ---------------------------- High-level helpers --------------------------- */

  init(payload: InitPayload, timeoutMs?: number) {
    return this.send<{ id: number; type: "init"; payload: InitPayload }>({ type: "init", payload }, timeoutMs);
  }

  install(payload: InstallPayload, timeoutMs?: number) {
    return this.send<{ id: number; type: "install"; payload: InstallPayload }>({ type: "install", payload }, timeoutMs);
  }

  call(fqfn: string, args?: any[], kwargs?: Record<string, any>, timeoutMs?: number) {
    return this.send<{ id: number; type: "call"; payload: CallPayload }>(
      { type: "call", payload: { fqfn, args, kwargs } },
      timeoutMs
    );
  }

  runPython(code: string, payload?: any, timeoutMs?: number) {
    return this.send<{ id: number; type: "runPython"; payload: RunPythonPayload }>(
      { type: "runPython", payload: { code, payload } },
      timeoutMs
    );
  }

  version(timeoutMs?: number) {
    return this.send<{ id: number; type: "version" }>({ type: "version" }, timeoutMs);
  }
}

/* --------------------------------- Utilities -------------------------------- */

/** Creates a reasonably unique, monotonic id function with a short random prefix. */
export function makeIdFactory(): () => string {
  const prefix = Math.random().toString(36).slice(2, 8);
  let n = 0;
  return () => `${prefix}-${++n}`;
}

/**
 * Convenience to construct a Worker (module type) with friendly defaults.
 * Consumers may still provide their own Worker, but this is handy for apps.
 */
export function createModuleWorker(scriptUrl: string | URL, opts?: WorkerOptions) {
  const options: WorkerOptions = { type: "module", ...opts };
  return new Worker(scriptUrl, options);
}
