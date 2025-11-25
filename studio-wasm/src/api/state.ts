/**
 * Ephemeral state API (in-worker)
 * ===============================
 * Lightweight wrapper for keeping per-session contract state inside the Pyodide
 * worker. Falls back to stateless simulation when the Python bridge does not
 * expose stateful helpers.
 */

import type { CompiledContract, Json, SimulateCallResult, SimulateDeployResult } from "./simulator";
import { ensurePyReady, simulateCall, simulateDeploy } from "./simulator";

/* ---------------------------------- Types ---------------------------------- */

export interface StateInit {
  /** Optionally deploy a contract into this state immediately. */
  deploy?: {
    compiled: CompiledContract;
    manifest: Json;
    /** Optional initializer name; defaults to "init". */
    initMethod?: string;
    /** Initializer args. */
    initArgs?: any[] | Record<string, any>;
    context?: Json;
    gasLimit?: number;
  };
  /** Optional seed snapshot to restore into this new state. */
  fromSnapshotBase64?: string;
  /** Verbose boot logs (passed to worker init). */
  verbose?: boolean;
  /** Optional Pyodide base URL / packages; forwarded to ensurePyReady. */
  pyodideBaseUrl?: string;
  packages?: string[];
  requirementsText?: string;
  files?: Record<string, string>;
}

export interface CallParams {
  compiled: CompiledContract;
  manifest: Json;
  method: string;
  args?: any[] | Record<string, any>;
  context?: Json;
  gasLimit?: number;
}

export interface DeployParams {
  compiled: CompiledContract;
  manifest: Json;
  initMethod?: string;
  initArgs?: any[] | Record<string, any>;
  context?: Json;
  gasLimit?: number;
}

export interface StateSnapshot {
  /** Opaque snapshot (base64) as returned by the Python bridge. */
  base64: string;
}

export interface StateHandle {
  /** Opaque state identifier scoped to the worker lifetime. */
  id: string;
  /** Run a read/write call against this state's storage. */
  call(params: CallParams): Promise<SimulateCallResult>;
  /** Deploy a contract into this state (initialization call). */
  deploy(params: DeployParams): Promise<SimulateDeployResult>;
  /** Capture an opaque snapshot that can be restored later. */
  snapshot(): Promise<StateSnapshot>;
  /** Restore a previously captured snapshot into this state. */
  restore(snap: StateSnapshot): Promise<void>;
  /** Drop/free this state on the worker (best-effort). */
  drop(): Promise<void>;
}

/* --------------------------------- Internals -------------------------------- */

function genId(): string {
  const a = new Uint8Array(16);
  (globalThis.crypto || require("crypto").webcrypto).getRandomValues(a);
  return [...a].map((b) => b.toString(16).padStart(2, "0")).join("");
}

function initToWorker(init?: StateInit) {
  return init
    ? {
        baseUrl: init.pyodideBaseUrl,
        verbose: init.verbose,
        packages: init.packages,
        requirementsText: init.requirementsText,
        files: init.files,
      }
    : undefined;
}

function toStatelessInit(init?: StateInit) {
  return init
    ? {
        pyodideBaseUrl: init.pyodideBaseUrl,
        packages: init.packages,
        requirementsText: init.requirementsText,
        files: init.files,
        verbose: init.verbose,
      }
    : undefined;
}

function normalizeArgs(args: any[] | Record<string, any> | undefined, manifest: Json, method: string): any[] {
  if (Array.isArray(args)) return args;
  if (!args || typeof args !== "object") return [];

  const fn = manifest?.abi?.functions?.find((f: any) => f?.name === method);
  if (fn?.inputs && Array.isArray(fn.inputs)) {
    return fn.inputs.map((inp: any) => (args as any)[inp.name]);
  }

  return Object.values(args);
}

/**
 * Detect whether the Python bridge exposes the stateful API. We memoize per worker boot.
 */
let _hasStateApi: boolean | null = null;
async function ensureStateCapability(): Promise<boolean> {
  if (_hasStateApi !== null) return _hasStateApi;
  try {
    const client = await ensurePyReady();
    // Try a harmless probe: call a non-mutating helper if present; otherwise attempt state_new/drop.
    try {
      const ok = await client.call("bridge.entry.state_capabilities", [], {}, 10_000);
      _hasStateApi = Boolean(ok?.stateful === true || ok === true);
      if (_hasStateApi) return _hasStateApi;
    } catch {
      // ignore and try creating/dropping a scratch state
    }
    try {
      const res = await client.call("bridge.entry.state_new", [], {}, 10_000);
      const tmpId = String(res?.state_id ?? res?.id ?? res);
      if (tmpId) {
        await client.call("bridge.entry.state_drop", [], { state_id: tmpId }, 10_000);
        _hasStateApi = true;
        return true;
      }
    } catch {
      // fall through
    }
    _hasStateApi = false;
    return false;
  } catch {
    _hasStateApi = false;
    return false;
  }
}

/* ---------------------------------- API ---------------------------------- */

/**
 * Create a new ephemeral state on the worker.
 * Optionally seeds from a snapshot and/or deploys a contract immediately.
 */
export async function createState(init?: StateInit): Promise<StateHandle> {
  const client = await ensurePyReady(initToWorker(init));
  const stateful = await ensureStateCapability();

  // Create state (or a synthetic stateless handle)
  let id = "stateless-default";
  if (stateful) {
    const created = await client.call(
      "bridge.entry.state_new",
      [],
      init?.fromSnapshotBase64 ? { snapshot_b64: init.fromSnapshotBase64 } : {},
      15_000
    );
    id = String(created?.state_id ?? created?.id ?? created ?? genId());
  }

  const handle: StateHandle = {
    id,

    call: async (params: CallParams): Promise<SimulateCallResult> => {
      const args = normalizeArgs(params.args, params.manifest, params.method);
      if (stateful) {
        try {
          const res = await client.call(
            "bridge.entry.simulate_tx",
            [params.manifest, params.compiled.ir, params.method, args],
            {
              kind: "call",
              state_id: id,
              gas_limit: params.gasLimit ?? 500_000,
              ctx: params.context,
            },
            120_000
          );
          return {
            ok: res?.ok !== false,
            returnValue: res?.return_value ?? res?.return ?? null,
            gasUsed: res?.gas_used ?? 0,
            events: (res?.events ?? []) as SimulateCallResult["events"],
            logs: res?.logs ?? undefined,
          };
        } catch (e) {
          return { ok: false, error: e, gasUsed: 0, events: [] } as SimulateCallResult;
        }
      }

      // Stateless fallback
      return simulateCall({
        compiled: params.compiled,
        manifest: params.manifest,
        method: params.method,
        args,
        context: params.context,
        gasLimit: params.gasLimit,
        init: toStatelessInit(init),
      });
    },

    deploy: async (params: DeployParams): Promise<SimulateDeployResult> => {
      const method = params.initMethod ?? "init";
      const args = normalizeArgs(params.initArgs, params.manifest, method);
      if (stateful) {
        try {
          const res = await client.call(
            "bridge.entry.simulate_tx",
            [params.manifest, params.compiled.ir, method, args],
            {
              kind: "deploy",
              state_id: id,
              gas_limit: params.gasLimit ?? 500_000,
              ctx: params.context,
            },
            120_000
          );
          return {
            ok: res?.ok !== false,
            gasUsed: res?.gas_used ?? 0,
            codeHash: res?.code_hash ?? params.compiled.codeHash,
            codeSize: typeof res?.code_size === "number" ? res.code_size : undefined,
            logs: res?.logs ?? undefined,
            error: res?.ok === false ? res?.error : undefined,
          };
        } catch (e) {
          return { ok: false, gasUsed: 0, error: e };
        }
      }

      // Stateless fallback
      return simulateDeploy({
        compiled: params.compiled,
        manifest: params.manifest,
        initMethod: method,
        initArgs: args,
        context: params.context,
        gasLimit: params.gasLimit,
        init: toStatelessInit(init),
      });
    },

    snapshot: async (): Promise<StateSnapshot> => {
      if (!stateful) {
        // Stateless has no persistent storage; return empty sentinel snapshot.
        return { base64: "" };
      }
      const snap = await client.call(
        "bridge.entry.state_snapshot",
        [],
        { state_id: id },
        20_000
      );
      const base64 = String(snap?.snapshot_b64 ?? snap?.b64 ?? "");
      return { base64 };
    },

    restore: async (snap: StateSnapshot): Promise<void> => {
      if (!stateful) return;
      await client.call(
        "bridge.entry.state_restore",
        [],
        { state_id: id, snapshot_b64: snap.base64 },
        30_000
      );
    },

    drop: async (): Promise<void> => {
      if (!stateful) return;
      try {
        await client.call("bridge.entry.state_drop", [], { state_id: id }, 10_000);
      } catch {
        /* best-effort */
      }
    },
  };

  // Optional immediate deploy
  if (init?.deploy) {
    await handle.deploy({
      compiled: init.deploy.compiled,
      manifest: init.deploy.manifest,
      initMethod: init.deploy.initMethod,
      initArgs: init.deploy.initArgs,
      context: init.deploy.context,
      gasLimit: init.deploy.gasLimit,
    });
  }

  return handle;
}

/* Convenience: create, deploy, use, and auto-drop via a scoped helper. */
export async function withState<T>(
  init: StateInit | undefined,
  fn: (s: StateHandle) => Promise<T>
): Promise<T> {
  const s = await createState(init);
  try {
    return await fn(s);
  } finally {
    await s.drop();
  }
}

export default {
  createState,
  withState,
};
