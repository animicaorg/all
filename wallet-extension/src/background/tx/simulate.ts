/**
 * Preflight simulation for transactions.
 *
 * Tries one or more JSON-RPC methods (in order) to obtain a dry-run result:
 *   1) "omni_simulate"      — preferred Animica method (tx as structured JSON/CBOR-friendly)
 *   2) "omni_txSimulate"    — legacy alias
 *   3) "omni_call"          — read-only call style (deploy/call only; may not handle transfer)
 *
 * Returns a normalized SimulateResult with status, gasUsed, logs, and optional returnData.
 *
 * NOTE: This module does NOT sign or submit — it only simulates. Use tx/build + tx/sign + tx/submit
 * for the full send pipeline.
 */

import type { TxBody } from "./types";
import { getRpcClient } from "../network/rpc";

export type Bytes = Uint8Array;

export interface SimulateResult {
  success: boolean;
  gasUsed: number;
  /** VM or contract-emitted logs/events (already decoded by node if supported). */
  logs?: unknown[];
  /** Raw return bytes from a call (if any). */
  returnData?: Bytes;
  /** Optional access list or trace fragments (scheduler-dependent). */
  accessList?: unknown;
  /** Optional state diff summary (node-dependent; non-consensus). */
  stateDiff?: unknown;
  /** Error information when success=false or RPC failed. */
  error?: { code?: number; message: string; data?: unknown };
}

export interface PreflightOptions {
  /**
   * Block tag to simulate against. "pending" often includes mempool effects,
   * while "latest" is the last sealed head. Default: "latest".
   */
  at?: "latest" | "pending";
  /**
   * Prefer a specific RPC shape if you know your node’s surface.
   * If not set, we attempt the known variants in a safe order.
   */
  prefer?: "omni_simulate" | "omni_txSimulate" | "omni_call";
  /**
   * Ask node to include traces/access-lists if available.
   */
  includeTraces?: boolean;
}

export async function simulateTxPreflight(
  body: TxBody,
  opts: PreflightOptions = {}
): Promise<SimulateResult> {
  const rpc = getRpcClient();
  const at = opts.at ?? "latest";

  // Define candidate RPC calls in priority order.
  const candidates: Array<() => Promise<any>> = [];

  const wantTraces = !!opts.includeTraces;

  const pushOmniSim = () =>
    rpc.request("omni_simulate", [
      {
        tx: body, // structured TxBody (node side may accept CBOR too; JSON is fine here)
        at,
        options: { includeTraces: wantTraces },
      },
    ]);

  const pushOmniSimLegacy = () =>
    rpc.request("omni_txSimulate", [
      body,
      { at, includeTraces: wantTraces },
    ]);

  // "omni_call" shape: closer to eth_call semantics; only valid for call/deploy kinds.
  const pushOmniCall = () =>
    rpc.request("omni_call", [
      {
        from: (body as any).from, // best-effort mapping (the node may ignore for static calls)
        to: (body as any).to ?? null,
        value: (body as any).amount ?? 0,
        data: (body as any).data ?? (body as any).input ?? null,
      },
      at,
      { includeTraces: wantTraces },
    ]);

  // Respect preference if set; otherwise try all in order.
  const order =
    opts.prefer === "omni_call"
      ? [pushOmniCall, pushOmniSim, pushOmniSimLegacy]
      : opts.prefer === "omni_txSimulate"
      ? [pushOmniSimLegacy, pushOmniSim, pushOmniCall]
      : [pushOmniSim, pushOmniSimLegacy, pushOmniCall];

  // Try candidates until one succeeds or all fail.
  let lastErr: any | undefined;
  for (const fn of order) {
    try {
      const res = await fn();
      return normalizeResult(res);
    } catch (e: any) {
      lastErr = e;
      if (!isMethodNotFound(e)) {
        // If it's not a "method not found", treat as a terminal simulation failure.
        return {
          success: false,
          gasUsed: 0,
          error: { code: e?.code, message: String(e?.message ?? e), data: e?.data },
        };
      }
      // else: fall through to next candidate
    }
  }

  // If we’re here, all methods were missing.
  return {
    success: false,
    gasUsed: 0,
    error: {
      code: lastErr?.code ?? -32601,
      message:
        "No supported simulation method on RPC (tried omni_simulate / omni_txSimulate / omni_call).",
      data: lastErr?.data,
    },
  };
}

/* --------------------------------- Helpers -------------------------------- */

function isMethodNotFound(e: any): boolean {
  const msg = String(e?.message ?? "");
  return e?.code === -32601 || /method not found/i.test(msg);
}

/**
 * Accepts various node-specific simulate responses and produces a stable shape.
 *
 * Expected shapes supported:
 *  - { success, gasUsed, logs?, returnData?, accessList?, stateDiff? }
 *  - { ok, gas_used, logs, return, access_list, state_diff }
 *  - eth-like: { status, gasUsed, logs, output }
 */
function normalizeResult(raw: any): SimulateResult {
  if (!raw || typeof raw !== "object") {
    return {
      success: false,
      gasUsed: 0,
      error: { message: "Invalid simulate result shape" },
    };
  }

  // Multiple naming conventions supported:
  const success =
    coalesceBool(raw.success, raw.ok, raw.status === 1, raw.status === "0x1") ?? false;

  const gasUsed =
    num(raw.gasUsed) ?? num(raw.gas_used) ?? num(raw.gas_used_total) ?? 0;

  const logs = raw.logs ?? raw.events ?? undefined;

  const returnData: Bytes | undefined =
    asBytes(raw.returnData) ??
    asBytes(raw.return) ??
    asHexBytes(raw.output) ??
    undefined;

  const accessList = raw.accessList ?? raw.access_list ?? undefined;
  const stateDiff = raw.stateDiff ?? raw.state_diff ?? undefined;

  return { success, gasUsed, logs, returnData, accessList, stateDiff };
}

function coalesceBool(...xs: Array<any>): boolean | undefined {
  for (const x of xs) {
    if (typeof x === "boolean") return x;
    if (typeof x === "number") return x !== 0;
    if (typeof x === "string") {
      if (x === "0x0" || x === "0") return false;
      if (x === "0x1" || x === "1") return true;
    }
  }
  return undefined;
}

function num(x: any): number | undefined {
  if (typeof x === "number" && Number.isFinite(x)) return x;
  if (typeof x === "string") {
    if (x.startsWith("0x")) return Number.parseInt(x, 16);
    const n = Number(x);
    if (Number.isFinite(n)) return n;
  }
  return undefined;
}

function asBytes(x: any): Bytes | undefined {
  if (x instanceof Uint8Array) return x;
  return undefined;
}

function asHexBytes(x: any): Bytes | undefined {
  if (typeof x !== "string") return undefined;
  const hex = x.startsWith("0x") ? x.slice(2) : x;
  if (hex.length % 2 !== 0) return undefined;
  const out = new Uint8Array(hex.length / 2);
  for (let i = 0; i < out.length; i++) {
    const b = Number.parseInt(hex.slice(i * 2, i * 2 + 2), 16);
    if (Number.isNaN(b)) return undefined;
    out[i] = b;
  }
  return out;
}
