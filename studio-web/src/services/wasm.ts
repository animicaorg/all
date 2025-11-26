/**
 * WASM (Pyodide) simulator bridge used by studio-web.
 *
 * This module lazily loads @animica/studio-wasm and exposes a small,
 * app-friendly facade for:
 *  - compileSource(source, manifest)
 *  - simulateCall({ source|ir, manifest, fn, args, seed })
 *  - estimateGas({ source|ir, manifest, fn, args })
 *  - resetState()  (ephemeral state inside the worker)
 *
 * The underlying library runs in a dedicated Worker and is safe to call
 * from the UI thread. We keep a singleton here for the app.
 */

export type Diagnostic = {
  message: string;
  severity: 'error' | 'warning' | 'info';
  line?: number;
  column?: number;
};

export type CompileResult = {
  ok: boolean;
  ir?: Uint8Array;                 // encoded IR (CBOR/msgpack), if ok
  gasUpperBound?: number;          // static estimate (upper bound)
  diagnostics: Diagnostic[];       // empty when ok
  codeHash?: string;               // optional digest of compiled code
};

export type SimulateCallParams = {
  source?: string;                 // provide either source+manifest or ir+manifest
  ir?: Uint8Array;
  manifest: Record<string, unknown>;
  fn: string;
  args?: unknown[];
  seed?: string | number;          // deterministic PRNG seed, optional
};

export type EstimateGasParams = Omit<SimulateCallParams, 'seed'>;

export type EventLog = { name: string; args: Record<string, unknown> };

export type SimulateResult = {
  ok: boolean;
  return?: unknown;
  logs?: EventLog[];
  gasUsed?: number;
  error?: string;
};

export type WasmFacade = {
  boot(): Promise<void>;
  compileSource(source: string, manifest: Record<string, unknown>): Promise<CompileResult>;
  simulateCall(p: SimulateCallParams): Promise<SimulateResult>;
  estimateGas(p: EstimateGasParams): Promise<number>;
  resetState(): Promise<void>;
};

/* ----------------------------------------------------------------------------
 * Internal singleton wiring
 * -------------------------------------------------------------------------- */

let _ready: Promise<void> | null = null;
let _lib: any = null;            // @animica/studio-wasm (typed as any to remain forward-compatible)
let _state: any = null;          // ephemeral state handle inside worker (created by the lib)

/**
 * Ensure the WASM library is loaded and initialized.
 * This is idempotent and safe to call multiple times.
 */
async function boot(): Promise<void> {
  if (_ready) return _ready;
  _ready = (async () => {
    // Dynamic import to avoid bundling in SSR / non-browser contexts.
    _lib = await import('@animica/studio-wasm').catch(async () => {
      // Fallback to the local shim when the package isn't available.
      return await import('../sdk-shim/studio-wasm');
    });

    // The library exposes a few namespaces; we handle both "named API" and "flat" exports.
    // Create/reset ephemeral state used by simulator (keeps storage/events in the worker).
    _state = _lib?.state?.create?.() ?? (_lib?.createState ? _lib.createState() : null);

    // If the lib requires an explicit boot (Pyodide preload), perform it.
    const maybeBoot =
      _lib?.boot ??
      _lib?.load ??
      _lib?.loadPyodide ??
      _lib?.pyodide?.load ??
      null;

    if (typeof maybeBoot === 'function') {
      await maybeBoot();
    }

    // Some builds separate init step for mounting the VM package into Pyodide.
    const maybeInit =
      _lib?.init ??
      _lib?.pyodide?.init ??
      _lib?.initPy ??
      null;

    if (typeof maybeInit === 'function') {
      await maybeInit();
    }
  })();

  return _ready;
}

/* ----------------------------------------------------------------------------
 * Public facade (thin wrappers around library calls)
 * -------------------------------------------------------------------------- */

async function compileSource(
  source: string,
  manifest: Record<string, unknown>
): Promise<CompileResult> {
  await boot();

  // Prefer namespaced compiler API if present.
  const fn =
    _lib?.compiler?.compileSource ??
    _lib?.compileSource;

  if (typeof fn !== 'function') {
    return {
      ok: false,
      diagnostics: [{ message: 'compileSource API not available', severity: 'error' }],
    };
  }

  try {
    const res = await fn(source, manifest);
    // Normalize shape to CompileResult
    return {
      ok: !!res?.ok ?? true,
      ir: res?.ir ?? res?.bytecode ?? res?.program ?? undefined,
      gasUpperBound: res?.gasUpperBound ?? res?.gas?.upperBound ?? undefined,
      diagnostics: res?.diagnostics ?? [],
      codeHash: res?.codeHash,
    };
  } catch (err: any) {
    return {
      ok: false,
      diagnostics: [
        { message: err?.message ?? String(err), severity: 'error' },
      ],
    };
  }
}

async function simulateCall(p: SimulateCallParams): Promise<SimulateResult> {
  await boot();

  const fn =
    _lib?.simulator?.simulateCall ??
    _lib?.simulateCall;

  if (typeof fn !== 'function') {
    return { ok: false, error: 'simulateCall API not available' };
  }

  // The worker-backed API generally accepts (state, {source|ir, manifest, fn, args, seed})
  try {
    const payload = { ...p };
    const res = await (hasStateParam(fn) ? fn(_state, payload) : fn(payload));

    // Normalize
    return {
      ok: !!res?.ok ?? true,
      return: res?.return ?? res?.result ?? undefined,
      logs: res?.logs ?? [],
      gasUsed: res?.gasUsed ?? res?.gas?.used ?? undefined,
      error: res?.error,
    };
  } catch (err: any) {
    return { ok: false, error: err?.message ?? String(err) };
  }
}

async function estimateGas(p: EstimateGasParams): Promise<number> {
  await boot();

  const fn =
    _lib?.simulator?.estimateGas ??
    _lib?.estimateGas;

  if (typeof fn !== 'function') {
    // Fallback: run simulateCall and take gasUsed if available
    const sim = await simulateCall({ ...p, seed: '0' });
    if (sim.ok && typeof sim.gasUsed === 'number') return sim.gasUsed;
    throw new Error('estimateGas API not available');
  }

  const payload = { ...p };
  const res = await (hasStateParam(fn) ? fn(_state, payload) : fn(payload));
  const gas = res?.gas ?? res?.gasUsed ?? res;
  if (typeof gas !== 'number') {
    // Normalize from shapes like { gas: { upperBound: N } }
    const maybe = res?.gas?.upperBound ?? res?.upperBound;
    if (typeof maybe === 'number') return maybe;
    throw new Error('estimateGas: unexpected response shape');
  }
  return gas;
}

async function resetState(): Promise<void> {
  await boot();
  const destroy = _lib?.state?.destroy ?? _lib?.destroyState;
  const create = _lib?.state?.create ?? _lib?.createState;

  if (typeof destroy === 'function' && _state) {
    try { await destroy(_state); } catch { /* ignore */ }
  }
  _state = typeof create === 'function' ? await create() : null;
}

/* --------------------------------- Helpers -------------------------------- */

function hasStateParam(fn: Function): boolean {
  // Heuristic: some APIs accept (state, payload). We detect by arity >= 2.
  return fn.length >= 2;
}

/* --------------------------------- Singleton -------------------------------- */

let _facade: WasmFacade | null = null;

/** Get the app-wide WASM simulator facade. */
export function getWasm(): WasmFacade {
  if (_facade) return _facade;
  _facade = { boot, compileSource, simulateCall, estimateGas, resetState };
  return _facade;
}

export default getWasm;
