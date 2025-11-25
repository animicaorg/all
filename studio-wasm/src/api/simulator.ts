/**
 * Simulator API
 * =============
 * High-level helpers that talk to the Pyodide-backed Python VM running inside
 * the dedicated worker. Provides:
 *  - simulateCall: run a contract method locally (no state writes)
 *  - simulateDeploy: dry-run a deploy (compile + estimate gas)
 *  - estimateGas: helper for either call/deploy
 *
 * The worker must support:
 *  - bridge.entry.version()
 *  - bridge.entry.run_call(source: str, manifest: dict, method: str, args: list)
 *  - bridge.entry.simulate_tx(kind: "deploy"|"call", manifest: dict, source?: str,
 *        method?: str, args?: list)
 *  - bridge.entry.compile_bytes(source: bytes, manifest: dict)
 */

import { PyVmWorkerClient, createModuleWorker } from "../worker/protocol";

type Json = Record<string, any>;

export interface EventLog {
  name: string;
  args: Record<string, any>;
}

export interface SimulateCallParams {
  /** Python contract source code (utf-8). */
  source: string;
  /** Contract manifest JSON (ABI + metadata). */
  manifest: Json;
  /** Method to call. */
  method: string;
  /** Positional arguments for the method. */
  args?: any[];
  /** Optional init options for the underlying Pyodide worker. */
  init?: SimulatorInit;
}

export interface SimulateCallResult {
  returnValue: any;
  gasUsed: number;
  events: EventLog[];
  logs?: string[]; // optional debug logs from the VM
}

export interface SimulateDeployParams {
  source: string;
  manifest: Json;
  /** Optional initializer function name (default: "init") */
  initMethod?: string;
  /** Optional initializer args */
  initArgs?: any[];
  init?: SimulatorInit;
}

export interface SimulateDeployResult {
  gasUsed: number;
  /** Hex (0x…) code hash if available from compile step. */
  codeHash?: string;
  /** Size in bytes of compiled artifact, if available. */
  codeSize?: number;
}

export type EstimateGasMode = "call" | "deploy";

export interface EstimateGasParamsCall {
  mode: "call";
  source: string;
  manifest: Json;
  method: string;
  args?: any[];
  init?: SimulatorInit;
}

export interface EstimateGasParamsDeploy {
  mode: "deploy";
  source: string;
  manifest: Json;
  initMethod?: string;
  initArgs?: any[];
  init?: SimulatorInit;
}

export type EstimateGasParams = EstimateGasParamsCall | EstimateGasParamsDeploy;

export interface GasEstimateResult {
  gasUsed: number;
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

function ensureArray<T>(v: T[] | undefined): T[] {
  return Array.isArray(v) ? v : [];
}

function toSimError(prefix: string, err: unknown): Error {
  if (err instanceof Error) {
    err.message = `${prefix}: ${err.message}`;
    return err;
  }
  return new Error(`${prefix}: ${String(err)}`);
}

/* --------------------------------- API impl -------------------------------- */

export async function simulateCall(params: SimulateCallParams): Promise<SimulateCallResult> {
  const { source, manifest, method } = params;
  const args = ensureArray(params.args);
  try {
    const client = await ensurePyReady(params.init);
    const result = await client.call(
      "bridge.entry.run_call",
      [],
      { source, manifest, method, args },
      120_000
    );
    // Expected shape (from Python bridge):
    // { return_value: any, gas_used: int, events: [{name, args}, ...], logs?: [str] }
    return {
      returnValue: result?.return_value ?? null,
      gasUsed: result?.gas_used ?? 0,
      events: (result?.events ?? []) as EventLog[],
      logs: result?.logs ?? undefined,
    };
  } catch (e) {
    throw toSimError("simulateCall failed", e);
  }
}

export async function simulateDeploy(params: SimulateDeployParams): Promise<SimulateDeployResult> {
  const { source, manifest } = params;
  const initMethod = params.initMethod ?? "init";
  const initArgs = ensureArray(params.initArgs);

  try {
    const client = await ensurePyReady(params.init);

    // First, try to get code hash/size from compile step (best-effort).
    let codeHash: string | undefined;
    let codeSize: number | undefined;
    try {
      const comp = await client.call(
        "bridge.entry.compile_bytes",
        [],
        { source, manifest },
        90_000
      );
      codeHash = comp?.code_hash;
      codeSize = typeof comp?.code_size === "number" ? comp.code_size : undefined;
    } catch {
      // ignore compile hash error; gas sim may still work (bridge may compile internally)
    }

    const sim = await client.call(
      "bridge.entry.simulate_tx",
      [],
      { kind: "deploy", source, manifest, method: initMethod, args: initArgs },
      120_000
    );
    // Expected: { gas_used: int }
    return {
      gasUsed: sim?.gas_used ?? 0,
      codeHash,
      codeSize,
    };
  } catch (e) {
    throw toSimError("simulateDeploy failed", e);
  }
}

export async function estimateGas(params: EstimateGasParams): Promise<GasEstimateResult> {
  try {
    const client = await ensurePyReady(params.init);

    if (params.mode === "call") {
      const res = await client.call(
        "bridge.entry.simulate_tx",
        [],
        {
          kind: "call",
          source: params.source,
          manifest: params.manifest,
          method: params.method,
          args: ensureArray(params.args),
          estimate_only: true,
        },
        120_000
      );
      return { gasUsed: res?.gas_used ?? 0 };
    } else {
      const res = await client.call(
        "bridge.entry.simulate_tx",
        [],
        {
          kind: "deploy",
          source: params.source,
          manifest: params.manifest,
          method: params.initMethod ?? "init",
          args: ensureArray(params.initArgs),
          estimate_only: true,
        },
        120_000
      );
      return { gasUsed: res?.gas_used ?? 0 };
    }
  } catch (e) {
    throw toSimError("estimateGas failed", e);
  }
}

/* --------------------------------- Exports --------------------------------- */

export default {
  ensurePyReady,
  simulateCall,
  simulateDeploy,
  estimateGas,
};
