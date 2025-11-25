/**
 * Simulator API
 * =============
 * High-level helpers that talk to the Pyodide-backed Python VM running inside
 * the dedicated worker. Provides:
 *  - simulateCall: run a contract method locally (optionally stateful)
 *  - simulateDeploy: dry-run a deploy
 *  - estimateGas: helper for either call/deploy
 */

import type { StateHandle } from "./state";
import { PyVmWorkerClient, createModuleWorker } from "../worker/protocol";

export type Json = Record<string, any>;

export interface EventLog {
  name: string;
  args: Record<string, any>;
}

export interface CompiledContract {
  ir: Uint8Array;
  codeHash?: string;
  gasUpperBound?: number;
  entry?: string;
  abi?: Json;
  manifest?: Json;
  diagnostics?: string[];
}

export interface SimulateCallParams {
  compiled: CompiledContract;
  manifest: Json;
  /** Method/entry name to invoke. */
  entry?: string;
  method?: string;
  /** Positional args or named args keyed by ABI param name. */
  args?: any[] | Record<string, any>;
  /** Optional execution context overrides (block/tx). */
  context?: Json;
  /** Optional gas limit override (defaults to 500k). */
  gasLimit?: number;
  /** Optional state handle for persistent storage. */
  state?: StateHandle;
  /** Optional init options for the underlying Pyodide worker. */
  init?: SimulatorInit;
}

export interface SimulateCallOk {
  ok: true;
  returnValue: any;
  gasUsed: number;
  events: EventLog[];
  logs?: string[];
  returnData?: Uint8Array;
}

export interface SimulateCallFail {
  ok: false;
  error: any;
  gasUsed: number;
  events: EventLog[];
  logs?: string[];
  returnData?: Uint8Array;
}

export type SimulateCallResult = SimulateCallOk | SimulateCallFail;

export interface SimulateDeployParams {
  compiled: CompiledContract;
  manifest: Json;
  /** Optional initializer function name (default: "init") */
  initMethod?: string;
  /** Optional initializer args */
  initArgs?: any[] | Record<string, any>;
  context?: Json;
  gasLimit?: number;
  init?: SimulatorInit;
  state?: StateHandle;
}

export interface SimulateDeployResult {
  ok: boolean;
  gasUsed: number;
  /** Hex (0x…) code hash if available from compile step. */
  codeHash?: string;
  /** Size in bytes of compiled artifact, if available. */
  codeSize?: number;
  logs?: string[];
  error?: any;
}

export type EstimateGasMode = "call" | "deploy";

export interface EstimateGasParamsCall {
  mode?: "call";
  compiled: CompiledContract;
  manifest: Json;
  entry?: string;
  method?: string;
  args?: any[] | Record<string, any>;
  context?: Json;
  gasLimit?: number;
  init?: SimulatorInit;
  state?: StateHandle;
}

export interface EstimateGasParamsDeploy {
  mode: "deploy";
  compiled: CompiledContract;
  manifest: Json;
  initMethod?: string;
  initArgs?: any[] | Record<string, any>;
  context?: Json;
  gasLimit?: number;
  init?: SimulatorInit;
  state?: StateHandle;
}

export type EstimateGasParams = EstimateGasParamsCall | EstimateGasParamsDeploy;

export interface GasEstimateResult {
  upperBound: number;
  lowerBound?: number;
  diagnostics?: string[];
}

/* ------------------------------ Worker wiring ------------------------------ */

let _client: PyVmWorkerClient | null = null;
let _ready = false;

/** Options to initialize the Pyodide/VM environment. */
export interface SimulatorInit {
  /** Base URL containing pyodide.{js,wasm,data}. If omitted, worker defaults apply. */
  pyodideBaseUrl?: string;
  /** Extra Python packages to install via micropip. */
  packages?: string[];
  /** Content of a requirements.txt to feed micropip (optional). */
  requirementsText?: string;
  /** Additional files to mount in the in-Py FS (path → file text). */
  files?: Record<string, string>;
  /** Verbose boot/install logs. */
  verbose?: boolean;
}

/** Create or reuse the worker client and ensure Pyodide is initialized. */
export async function ensurePyReady(init?: SimulatorInit): Promise<PyVmWorkerClient> {
  if (!_client) {
    const workerUrl = new URL("../worker/pyvm.worker.ts", import.meta.url);
    const worker = createModuleWorker(workerUrl);
    _client = new PyVmWorkerClient(worker, { timeoutMs: 180_000 });
  }
  if (!_ready) {
    await _client.init({
      baseUrl: init?.pyodideBaseUrl,
      verbose: init?.verbose,
      packages: init?.packages,
      requirementsText: init?.requirementsText,
      files: init?.files,
    });
    // Optional sanity check
    await _client.version(30_000);
    _ready = true;
  }
  return _client;
}

/* --------------------------------- Helpers --------------------------------- */

function normalizeArgs(args: any[] | Record<string, any> | undefined, manifest: Json, method: string): any[] {
  if (Array.isArray(args)) return args;
  if (!args || typeof args !== "object") return [];

  const fn = manifest?.abi?.functions?.find((f: any) => f?.name === method);
  if (fn?.inputs && Array.isArray(fn.inputs)) {
    return fn.inputs.map((inp: any) => (args as any)[inp.name]);
  }
  return Object.values(args);
}

function decodeBase64(b64: string): Uint8Array {
  if (typeof atob === "function") {
    const s = atob(b64);
    const out = new Uint8Array(s.length);
    for (let i = 0; i < s.length; i++) out[i] = s.charCodeAt(i);
    return out;
  }
  const maybeBuf = (globalThis as any).Buffer?.from?.(b64, "base64");
  if (maybeBuf) {
    const buf: any = maybeBuf;
    const offset = buf.byteOffset ?? 0;
    const length = buf.byteLength ?? buf.length ?? 0;
    return new Uint8Array(buf.buffer ?? buf, offset, length);
  }
  throw new Error("No base64 decoder available");
}

function decodeBytesBox(box: any): Uint8Array | undefined {
  if (!box || typeof box !== "object" || typeof box.__bytes_b64 !== "string") return undefined;
  return decodeBase64(box.__bytes_b64);
}

function mapCallResult(res: any): SimulateCallResult {
  const gasUsed = typeof res?.gas_used === "number" ? res.gas_used : 0;
  const events = (res?.events ?? []) as EventLog[];
  const logs = res?.logs ? [...(res.logs as any[])] : undefined;
  const returnData = decodeBytesBox(res?.return_data ?? res?.returnBytes);

  if (res?.ok === false) {
    return {
      ok: false,
      error: res.error ?? new Error("Simulation failed"),
      gasUsed,
      events,
      logs,
      returnData,
    };
  }

  return {
    ok: true,
    returnValue: res?.return ?? res?.return_value ?? res?.result ?? null,
    gasUsed,
    events,
    logs,
    returnData,
  };
}

function mapDeployResult(res: any, fallbackHash?: string): SimulateDeployResult {
  const gasUsed = typeof res?.gas_used === "number" ? res.gas_used : 0;
  const ok = res?.ok !== false;
  return {
    ok,
    gasUsed,
    codeHash: res?.code_hash ?? fallbackHash,
    codeSize: typeof res?.code_size === "number" ? res.code_size : undefined,
    logs: res?.logs ? [...(res.logs as any[])] : undefined,
    error: ok ? undefined : res?.error,
  };
}

function mapDiagnostics(v: any): string[] | undefined {
  const msgs = v?.diagnostics;
  if (!msgs) return undefined;
  if (Array.isArray(msgs)) return msgs.map(String);
  return [String(msgs)];
}

/* --------------------------------- API impl -------------------------------- */

export async function simulateCall(params: SimulateCallParams): Promise<SimulateCallResult> {
  const { compiled, manifest } = params;
  const method = params.method ?? params.entry ?? compiled.entry;
  if (!compiled?.ir) {
    return { ok: false, error: new Error("compiled IR is required"), gasUsed: 0, events: [] } as SimulateCallFail;
  }
  if (!method) {
    return { ok: false, error: new Error("method/entry is required"), gasUsed: 0, events: [] } as SimulateCallFail;
  }
  const argArray = normalizeArgs(params.args, manifest, method);

  try {
    if (params.state && typeof (params.state as any).call === "function") {
      return await (params.state as any).call({
        compiled,
        manifest,
        method,
        args: argArray,
        context: params.context,
        gasLimit: params.gasLimit,
      });
    }

    const client = await ensurePyReady(params.init);
    const res = await client.call(
      "bridge.entry.run_call",
      [compiled.ir, method, argArray],
      { gas_limit: params.gasLimit ?? 500_000, ctx: params.context },
      120_000
    );
    return mapCallResult(res);
  } catch (e) {
    return { ok: false, error: e, gasUsed: 0, events: [] };
  }
}

export async function simulateDeploy(params: SimulateDeployParams): Promise<SimulateDeployResult> {
  const { compiled, manifest } = params;
  const method = params.initMethod ?? "init";
  const argArray = normalizeArgs(params.initArgs, manifest, method);

  if (params.state && typeof (params.state as any).deploy === "function") {
    return await (params.state as any).deploy({
      compiled,
      manifest,
      initMethod: method,
      initArgs: argArray,
      context: params.context,
      gasLimit: params.gasLimit,
    });
  }

  try {
    const client = await ensurePyReady(params.init);
    const res = await client.call(
      "bridge.entry.simulate_tx",
      [manifest, compiled.ir, method, argArray],
      { kind: "deploy", gas_limit: params.gasLimit ?? 500_000, ctx: params.context },
      120_000
    );
    return mapDeployResult(res, compiled.codeHash);
  } catch (e) {
    return { ok: false, gasUsed: 0, error: e };
  }
}

export async function estimateGas(params: EstimateGasParams): Promise<GasEstimateResult> {
  const mode: EstimateGasMode = params.mode ?? "call";
  const compiled = params.compiled;
  const manifest = params.manifest;

  try {
    const client = await ensurePyReady(params.init);
    if (mode === "call") {
      const method = (params as EstimateGasParamsCall).method ?? (params as EstimateGasParamsCall).entry ?? compiled.entry;
      if (!method) return { upperBound: 0 };
      const args = normalizeArgs((params as EstimateGasParamsCall).args, manifest, method);
      const res = await client.call(
        "bridge.entry.simulate_tx",
        [manifest, compiled.ir, method, args],
        {
          kind: "call",
          estimate_only: true,
          gas_limit: (params as EstimateGasParamsCall).gasLimit ?? 500_000,
          ctx: (params as EstimateGasParamsCall).context,
        },
        120_000
      );
      return {
        upperBound: typeof res?.gas_used === "number" ? res.gas_used : 0,
        lowerBound: typeof res?.gas_lower_bound === "number" ? res.gas_lower_bound : undefined,
        diagnostics: mapDiagnostics(res),
      };
    }

    const method = (params as EstimateGasParamsDeploy).initMethod ?? "init";
    const args = normalizeArgs((params as EstimateGasParamsDeploy).initArgs, manifest, method);
    const res = await client.call(
      "bridge.entry.simulate_tx",
      [manifest, compiled.ir, method, args],
      {
        kind: "deploy",
        estimate_only: true,
        gas_limit: (params as EstimateGasParamsDeploy).gasLimit ?? 500_000,
        ctx: (params as EstimateGasParamsDeploy).context,
      },
      120_000
    );
    return {
      upperBound: typeof res?.gas_used === "number" ? res.gas_used : 0,
      lowerBound: typeof res?.gas_lower_bound === "number" ? res.gas_lower_bound : undefined,
      diagnostics: mapDiagnostics(res),
    };
  } catch (e) {
    return { upperBound: 0, diagnostics: [e instanceof Error ? e.message : String(e)] };
  }
}

/* --------------------------------- Exports --------------------------------- */

export default {
  ensurePyReady,
  simulateCall,
  simulateDeploy,
  estimateGas,
};
