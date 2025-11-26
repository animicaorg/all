/**
 * Simulate slice — prepares inputs (function + args), runs a local simulation
 * against the compiled IR via studio-wasm (Pyodide), and exposes results
 * including decoded events/logs and gas used.
 *
 * This integrates with ../services/wasm which should re-export the
 * studio-wasm high-level API. We defensively support a couple of shapes:
 *  - simulateCall(...) exported directly
 *  - getSimulator().simulateCall(...)
 *  - events.decode(...) or getEventsApi().decode(...)
 *
 * Dependencies:
 *  - The Compile slice typically produces IR bytes and (optionally) a manifest.
 *  - The Project slice may hold a manifest.json file we can parse for ABI.
 */

import useStore, { registerSlice, type SliceCreator, type StoreState, type SetState, type GetState } from './store';
import type { Diagnostic } from './compile';
import * as Wasm from '../services/wasm';

export interface SimEvent {
  name: string;
  args: Record<string, unknown>;
}

export interface SimResult {
  ok: boolean;
  returnValue?: unknown;
  returnHex?: string;        // optional raw encoding
  gasUsed?: number;
  events?: SimEvent[];
  logsRaw?: unknown[];       // if decoder not available, raw event tuples
  error?: string;
  diagnostics?: Diagnostic[];
}

export interface SimulateSlice {
  // inputs
  fn?: string;
  args: unknown[] | Record<string, unknown>;
  caller?: string;           // bech32/address if supported by simulator
  value?: string | number;   // optional value transfer for payable calls

  // state
  simStatus: 'idle' | 'running' | 'success' | 'error';
  gasUsed?: number;
  events: SimEvent[];
  result?: SimResult;
  error?: string;
  lastRunAt?: number;
  stateId?: string;          // ephemeral in-worker state handle (for persistent storage across calls)
  _reqId: number;            // internal: cancel stale runs

  // actions
  setFunction(name?: string): void;
  setArgs(args: unknown[] | Record<string, unknown>): void;
  setArgsJson(json: string): void;
  setCaller(addr?: string): void;
  setValue(v?: string | number): void;

  ensureState(): Promise<string>;
  resetState(): Promise<void>;

  /**
   * Run simulate. If ir/manifest are omitted, attempts to read them from
   * the Compile and Project slices (manifest.json).
   */
  run(opts?: {
    fn?: string;
    args?: unknown[] | Record<string, unknown>;
    caller?: string;
    value?: string | number;
    ir?: Uint8Array;
    manifest?: any;
  }): Promise<boolean>;

  /**
   * Ask simulator for a quick gas estimate without producing side effects.
   */
  estimateGas(opts?: {
    fn?: string;
    args?: unknown[] | Record<string, unknown>;
    ir?: Uint8Array;
    manifest?: any;
  }): Promise<number | undefined>;
}

function now(): number {
  return Date.now();
}

function toArrayArgs(args: unknown[] | Record<string, unknown>): unknown[] {
  if (Array.isArray(args)) return args;
  // Stable order: assume UI passed object using ABI order; we cannot infer
  // names→order reliably without ABI here, so fall back to Object.values.
  return Object.values(args ?? {});
}

async function wasmSimulator(): Promise<any> {
  // Use either direct exports or a factory
  const w: any = Wasm as any;
  if (typeof w.getSimulator === 'function') return await w.getSimulator();
  return {
    simulateCall: w.simulateCall,
    estimateGas: w.estimateGas,
    newState: w.newState,
    resetState: w.resetState,
    events: w.events,
    decodeEvents: w.decodeEvents,
  };
}

async function wasmEventsApi(): Promise<{ decode: (args: { manifest: any; logs: unknown[] }) => SimEvent[] } | null> {
  const w: any = Wasm as any;
  if (typeof w.getEventsApi === 'function') {
    try {
      const api = await w.getEventsApi();
      if (api && typeof api.decode === 'function') return api;
    } catch { /* ignore */ }
  }
  if (w.events && typeof w.events.decode === 'function') {
    return w.events as { decode: (args: { manifest: any; logs: unknown[] }) => SimEvent[] };
  }
  if (typeof w.decodeEvents === 'function') {
    return { decode: ({ manifest, logs }: any) => w.decodeEvents({ manifest, logs }) };
  }
  return null;
}

const simulateSlice: SliceCreator<SimulateSlice> = (set: SetState<StoreState>, get: GetState<StoreState>) => ({
  fn: undefined,
  args: [],
  caller: undefined,
  value: undefined,

  simStatus: 'idle',
  gasUsed: undefined,
  events: [],
  result: undefined,
  error: undefined,
  lastRunAt: undefined,
  stateId: undefined,
  _reqId: 0,

  setFunction(name?: string) {
    set({ fn: name } as Partial<StoreState>);
  },
  setArgs(args: unknown[] | Record<string, unknown>) {
    set({ args } as Partial<StoreState>);
  },
  setArgsJson(json: string) {
    try {
      const v = JSON.parse(json);
      if (Array.isArray(v) || (v && typeof v === 'object')) {
        set({ args: v } as Partial<StoreState>);
        return;
      }
      set({ error: 'Args JSON must be an array or object' } as Partial<StoreState>);
    } catch (e: any) {
      set({ error: `Invalid JSON: ${String(e?.message ?? e)}` } as Partial<StoreState>);
    }
  },
  setCaller(addr?: string) {
    set({ caller: addr } as Partial<StoreState>);
  },
  setValue(v?: string | number) {
    set({ value: v } as Partial<StoreState>);
  },

  async ensureState(): Promise<string> {
    const current = (get() as unknown as SimulateSlice).stateId;
    if (current) return current;
    const sim = await wasmSimulator();
    if (typeof sim?.newState === 'function') {
      const id = await sim.newState();
      set({ stateId: id } as Partial<StoreState>);
      return id;
    }
    // Fallback: use a dummy static id
    const id = 'default';
    set({ stateId: id } as Partial<StoreState>);
    return id;
  },

  async resetState(): Promise<void> {
    try {
      const sim = await wasmSimulator();
      const id = (get() as unknown as SimulateSlice).stateId;
      if (id && typeof sim?.resetState === 'function') {
        await sim.resetState({ stateId: id });
      }
    } catch { /* ignore */ }
    set({ stateId: undefined } as Partial<StoreState>);
  },

  async run(opts?: {
    fn?: string;
    args?: unknown[] | Record<string, unknown>;
    caller?: string;
    value?: string | number;
    ir?: Uint8Array;
    manifest?: any;
  }): Promise<boolean> {
    const myId = (get() as unknown as SimulateSlice)._reqId + 1;
    set({
      simStatus: 'running',
      error: undefined,
      result: undefined,
      events: [],
      gasUsed: undefined,
      _reqId: myId,
    } as Partial<StoreState>);

    // Resolve inputs
    const selFn = opts?.fn ?? (get() as unknown as SimulateSlice).fn;
    const selArgs = opts?.args ?? (get() as unknown as SimulateSlice).args;
    const caller = opts?.caller ?? (get() as unknown as SimulateSlice).caller;
    const value = opts?.value ?? (get() as unknown as SimulateSlice).value;

    // Obtain IR & manifest
    let ir = opts?.ir as Uint8Array | undefined;
    let manifest = opts?.manifest as any;

    if (!ir) {
      const s: any = get();
      ir = s?.ir ?? s?.compile?.ir ?? s?.['compile']?.ir; // tolerate different nestings
    }
    if (!manifest) {
      // try parse manifest from project files; prefer active dir manifest.json
      try {
        const s: any = get();
        const project = s as { files?: Record<string, { path: string; content: string }>; active?: string };
        const active = project.active;
        const files = project.files ?? {};
        const tryPaths: string[] = [];
        if (active) {
          const dir = active.includes('/') ? active.split('/').slice(0, -1).join('/') : '';
          if (dir) tryPaths.push(`${dir}/manifest.json`);
        }
        tryPaths.push('manifest.json');
        for (const p of tryPaths) {
          if (files[p]?.content) {
            manifest = JSON.parse(files[p].content);
            break;
          }
        }
      } catch { /* ignore */ }
    }

    if (!selFn) {
      set({ simStatus: 'error', error: 'No function selected' } as Partial<StoreState>);
      return false;
    }
    if (!ir) {
      set({ simStatus: 'error', error: 'No compiled IR available. Compile first.' } as Partial<StoreState>);
      return false;
    }
    if (!manifest) {
      set({ simStatus: 'error', error: 'No manifest/ABI available.' } as Partial<StoreState>);
      return false;
    }

    try {
      const sim = await wasmSimulator();
      const stateId = await (get() as unknown as SimulateSlice).ensureState();

      // Call into simulator
      const argsArray = toArrayArgs(selArgs);
      const invoke =
        typeof sim.simulateCall === 'function'
          ? sim.simulateCall
          : (payload: any) => {
              throw new Error('simulateCall not available from wasm bridge');
            };

      const res: any = await invoke({
        stateId,
        ir,
        manifest,
        entry: selFn,
        args: argsArray,
        caller,
        value,
      });

      if ((get() as unknown as SimulateSlice)._reqId !== myId) return false; // stale

      // Normalize result
      const ok: boolean = !!(res?.ok ?? (res?.error ? false : true));
      const gasUsed = res?.gasUsed ?? res?.gas ?? undefined;

      let events: SimEvent[] = [];
      let logsRaw: unknown[] | undefined = undefined;

      if (Array.isArray(res?.events)) {
        // Already decoded
        events = res.events as SimEvent[];
      } else if (Array.isArray(res?.logs)) {
        logsRaw = res.logs;
        // Try to decode using wasm events API
        try {
          const eventsApi = await wasmEventsApi();
          if (eventsApi) {
            events = (await eventsApi.decode({ manifest, logs: res.logs })) as SimEvent[];
          }
        } catch { /* ignore, keep raw */ }
      }

      const resultObj: SimResult = {
        ok,
        returnValue: res?.returnValue ?? res?.ret ?? res?.result,
        returnHex: res?.returnHex ?? res?.retHex,
        gasUsed,
        events,
        logsRaw,
        error: ok ? undefined : (res?.error ? String(res.error) : undefined),
        diagnostics: Array.isArray(res?.diagnostics) ? (res.diagnostics as Diagnostic[]) : undefined,
      };

      set({
        simStatus: ok ? 'success' : 'error',
        gasUsed,
        events,
        result: resultObj,
        error: ok ? undefined : resultObj.error ?? 'Simulation failed',
        lastRunAt: now(),
      } as Partial<StoreState>);

      return ok;
    } catch (e: any) {
      if ((get() as unknown as SimulateSlice)._reqId !== myId) return false; // stale
      set({
        simStatus: 'error',
        error: String(e?.message ?? e),
        lastRunAt: now(),
      } as Partial<StoreState>);
      return false;
    }
  },

  async estimateGas(opts?: {
    fn?: string;
    args?: unknown[] | Record<string, unknown>;
    ir?: Uint8Array;
    manifest?: any;
  }): Promise<number | undefined> {
    try {
      const sim = await wasmSimulator();

      const selFn = opts?.fn ?? (get() as unknown as SimulateSlice).fn;
      const selArgs = toArrayArgs(opts?.args ?? (get() as unknown as SimulateSlice).args);

      let ir = opts?.ir as Uint8Array | undefined;
      let manifest = opts?.manifest as any;

      if (!ir) {
        const s: any = get();
        ir = s?.ir ?? s?.compile?.ir ?? s?.['compile']?.ir;
      }
      if (!manifest) {
        try {
          const s: any = get();
          const project = s as { files?: Record<string, { path: string; content: string }>; active?: string };
          const active = project.active;
          const files = project.files ?? {};
          const tryPaths: string[] = [];
          if (active) {
            const dir = active.includes('/') ? active.split('/').slice(0, -1).join('/') : '';
            if (dir) tryPaths.push(`${dir}/manifest.json`);
          }
          tryPaths.push('manifest.json');
          for (const p of tryPaths) {
            if (files[p]?.content) {
              manifest = JSON.parse(files[p].content);
              break;
            }
          }
        } catch { /* ignore */ }
      }

      if (!selFn || !ir || !manifest) return undefined;

      const estimate =
        typeof sim.estimateGas === 'function'
          ? sim.estimateGas
          : async (_: any) => undefined;

      const est = await estimate({ ir, manifest, entry: selFn, args: selArgs });
      return typeof est === 'number' && Number.isFinite(est) ? est : undefined;
    } catch {
      return undefined;
    }
  },
});

registerSlice<SimulateSlice>(simulateSlice);

export const useSimulateStore = useStore;

export default undefined;
