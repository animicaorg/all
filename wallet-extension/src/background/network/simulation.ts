/**
 * Simulation utilities used by the wallet:
 *  - call(): read-only contract call (no state changes)
 *  - estimateGas(): static gas estimate for a call/tx
 *  - simulateTx(): full ApplyResult-style dry-run of a signed tx
 *
 * These functions talk to the node JSON-RPC and try multiple method names to
 * allow interop across node versions (omni_* preferred, animica_* fallback).
 */

import { RpcClient, RpcError, makeRpcClientForNetwork } from "./rpc";
import type { Network } from "./networks";

/** Hex string: "0x" + even-length lowercase hex */
export type Hex = `0x${string}`;

export interface CallRequest {
  from?: Hex;              // bech32/hex depending on node; hex is safest for RPC
  to: Hex;
  data?: Hex;              // ABI-encoded call data
  value?: Hex;             // optional value (wei-like units)
  gasLimit?: number;       // optional cap
  gasPrice?: Hex;          // optional
  accessList?: any[];      // optional EIP-2930-style if supported (ignored otherwise)
}

/** Minimal log/event shape used by simulator responses */
export interface SimLog {
  address?: Hex;
  name?: string;
  topics?: Hex[];
  data?: Hex;
  // Optionally decoded args (node-dependent); we keep 'any' to avoid tight coupling.
  args?: Record<string, unknown>;
}

/** Result of a read-only call (no state changes) */
export interface CallResult {
  ok: true;
  returnData: Hex; // ABI-encoded return payload (may be "0x")
  gasUsed?: number;
  logs?: SimLog[];
}
export interface CallError {
  ok: false;
  error: string;
  code?: number;
  data?: unknown;
}
export type CallResponse = CallResult | CallError;

/** Gas estimate result */
export interface EstimateResult {
  ok: true;
  gas: number;
  // Optional additional hints (why estimate may be capped, etc.)
  hint?: string;
}
export interface EstimateError {
  ok: false;
  error: string;
  code?: number;
  data?: unknown;
}
export type EstimateResponse = EstimateResult | EstimateError;

/** Full simulation result for a (signed) tx dry-run */
export interface SimulateResult {
  ok: true;
  success: boolean;     // true if not Revert/OOG/etc.
  gasUsed: number;
  returnData?: Hex;
  logs?: SimLog[];
  // Optional receipt-like fields (node-dependent)
  status?: number;      // 1/0 if provided
  reason?: string;      // revert reason (decoded message) if any
}
export interface SimulateError {
  ok: false;
  error: string;
  code?: number;
  data?: unknown;
}
export type SimulationResponse = SimulateResult | SimulateError;

// Preferred → fallback method names (to support older nodes)
const CALL_METHODS = ["omni_call", "animica_call"];
const ESTIMATE_METHODS = ["omni_estimateGas", "animica_estimateGas"];
const SIMULATE_METHODS = ["omni_simulateTx", "animica_simulateTx"];

/** Read-only call (no state writes). */
export async function call(
  net: Network,
  req: CallRequest,
  opts?: { timeoutMs?: number }
): Promise<CallResponse> {
  const client = makeRpcClientForNetwork(net, { timeoutMs: opts?.timeoutMs });
  try {
    const res = await tryRpcVariants<{
      returnData: Hex;
      gasUsed?: number;
      logs?: SimLog[];
    }>(client, CALL_METHODS, [normalizeCall(req)]);
    // Normalize shapes defensively
    const out: CallResult = {
      ok: true,
      returnData: ensureHex(res?.returnData ?? "0x"),
      gasUsed: numberOrUndefined(res?.gasUsed),
      logs: Array.isArray(res?.logs) ? res.logs : undefined,
    };
    return out;
  } catch (e: any) {
    return rpcErrToCallError(e);
  }
}

/** Static gas estimate for a call/tx. */
export async function estimateGas(
  net: Network,
  req: CallRequest,
  opts?: { timeoutMs?: number }
): Promise<EstimateResponse> {
  const client = makeRpcClientForNetwork(net, { timeoutMs: opts?.timeoutMs });
  try {
    const res = await tryRpcVariants<{ gas: number }>(client, ESTIMATE_METHODS, [normalizeCall(req)]);
    return { ok: true, gas: clampGas(res.gas) };
  } catch (e: any) {
    return rpcErrToEstimateError(e);
  }
}

/**
 * Dry-run a signed transaction against current state.
 * The exact payload schema depends on the node. We accept either:
 *  - { tx: Hex }   // CBOR-encoded signed tx hex
 *  - { raw: Hex }  // alias
 *  - or a structured object if the node supports it (will be forwarded)
 */
export async function simulateTx(
  net: Network,
  payload: { tx?: Hex; raw?: Hex } | Record<string, unknown>,
  opts?: { timeoutMs?: number }
): Promise<SimulationResponse> {
  const client = makeRpcClientForNetwork(net, { timeoutMs: opts?.timeoutMs });
  const arg = normalizeSimulateArg(payload);
  try {
    const res = await tryRpcVariants<{
      success: boolean;
      gasUsed: number;
      returnData?: Hex;
      logs?: SimLog[];
      status?: number;
      reason?: string;
    }>(client, SIMULATE_METHODS, [arg]);

    return {
      ok: true,
      success: !!res.success,
      gasUsed: clampGas(res.gasUsed ?? 0),
      returnData: res.returnData ? ensureHex(res.returnData) : undefined,
      logs: Array.isArray(res?.logs) ? res.logs : undefined,
      status: typeof res.status === "number" ? res.status : undefined,
      reason: typeof res.reason === "string" ? res.reason : undefined,
    };
  } catch (e: any) {
    return rpcErrToSimError(e);
  }
}

/* ----------------------------- internals ------------------------------ */

function normalizeCall(req: CallRequest): Record<string, unknown> {
  return {
    from: req.from,
    to: req.to,
    data: req.data ?? "0x",
    value: req.value,
    gasLimit: req.gasLimit,
    gasPrice: req.gasPrice,
    accessList: req.accessList,
  };
}

function normalizeSimulateArg(arg: { tx?: Hex; raw?: Hex } | Record<string, unknown>) {
  // Prefer { tx } if present; otherwise forward the object as-is.
  if (isHex((arg as any)?.tx)) return { tx: (arg as any).tx };
  if (isHex((arg as any)?.raw)) return { tx: (arg as any).raw };
  return arg;
}

/** Try a list of RPC method names until one succeeds or all fail (method-not-found is ignored). */
async function tryRpcVariants<T>(
  client: RpcClient,
  methods: string[],
  params: any[]
): Promise<T> {
  let lastErr: unknown = undefined;
  for (const m of methods) {
    try {
      // eslint-disable-next-line no-await-in-loop
      return await client.call<T>(m, params);
    } catch (e: any) {
      // -32601 = Method not found → try next
      if (e instanceof RpcError && e.code === -32601) {
        lastErr = e;
        continue;
      }
      // If server says "not implemented" or "disabled", some nodes use -320xx
      if (e instanceof RpcError && (e.code <= -32000 && e.code >= -32099)) {
        lastErr = e;
        continue;
      }
      // Other errors are propagated (call was recognized but failed)
      throw e;
    }
  }
  // If we exhausted all variants, bubble the last error (or synthesize one)
  if (lastErr) throw lastErr;
  throw new RpcError("No supported RPC method for requested operation", -32601);
}

function rpcErrToCallError(e: any): CallError {
  if (e instanceof RpcError) {
    return { ok: false, error: e.message, code: e.code, data: e.data };
  }
  return { ok: false, error: String(e?.message ?? e) };
}

function rpcErrToEstimateError(e: any): EstimateError {
  if (e instanceof RpcError) {
    return { ok: false, error: e.message, code: e.code, data: e.data };
  }
  return { ok: false, error: String(e?.message ?? e) };
}

function rpcErrToSimError(e: any): SimulateError {
  if (e instanceof RpcError) {
    return { ok: false, error: e.message, code: e.code, data: e.data };
  }
  return { ok: false, error: String(e?.message ?? e) };
}

function clampGas(v: number): number {
  if (!Number.isFinite(v) || v < 0) return 0;
  // Reasonable upper clamp to prevent accidental overflow in callers.
  const MAX = 10_000_000_000;
  return Math.min(Math.floor(v), MAX);
}

function ensureHex(x: unknown): Hex {
  if (typeof x === "string" && /^0x[0-9a-fA-F]*$/.test(x) && x.length % 2 === 0) {
    return (`0x${x.slice(2).toLowerCase()}`) as Hex;
  }
  if (typeof x === "string" && /^0x[0-9a-fA-F]*$/.test(x)) {
    // pad to even-length
    const body = x.slice(2);
    return (`0x${(body.length % 2 === 1 ? "0" : "") + body}`.toLowerCase()) as Hex;
  }
  // Fallback to empty
  return "0x";
}

function isHex(x: unknown): x is Hex {
  return typeof x === "string" && /^0x[0-9a-fA-F]*$/.test(x);
}

function numberOrUndefined(x: unknown): number | undefined {
  return typeof x === "number" && Number.isFinite(x) ? x : undefined;
}

export default {
  call,
  estimateGas,
  simulateTx,
};
