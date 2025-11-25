import { z } from 'zod';
import {
  Bech32mAddress,
  BytesLike,
  ChainId,
  JsonRpcRequest,
  JsonRpcResponse,
  Permission,
  NetworkConfig,
  SessionState,
  SignRequest,
  TxCommon,
  isHexString,
  isAddress,
  formatZodError,
  parseOrThrow,
} from './schema';

/* ───────────────────────── Assertions (type-narrowing) ───────────────────────── */

export function assert(cond: unknown, msg = 'assertion failed'): asserts cond {
  if (!cond) throw new Error(msg);
}

export function assertHex(value: unknown, label = 'hex'): asserts value is string {
  if (!isHexString(value)) {
    throw new Error(`${label} must be 0x-prefixed even-length hex`);
  }
}

export function assertAddress(value: unknown, label = 'address'): asserts value is string {
  if (!isAddress(value)) {
    throw new Error(`${label} invalid (expected bech32m anim1…)`);
  }
}

export function assertNonEmptyArray<T>(
  value: unknown,
  label = 'array'
): asserts value is T[] {
  if (!Array.isArray(value) || value.length === 0) {
    throw new Error(`${label} must be a non-empty array`);
  }
}

export function assertRange(n: number, { min, max, label = 'number' }: { min?: number; max?: number; label?: string }) {
  if (typeof n !== 'number' || Number.isNaN(n)) throw new Error(`${label} must be a number`);
  if (min !== undefined && n < min) throw new Error(`${label} must be ≥ ${min}`);
  if (max !== undefined && n > max) throw new Error(`${label} must be ≤ ${max}`);
}

/* ───────────────────────── Normalizers (return typed values) ─────────────────── */

export function ensureAddress(v: unknown, label = 'address'): string {
  return parseOrThrow(Bech32mAddress, v, label);
}

export function ensureChainId(v: unknown, label = 'chainId'): number {
  return parseOrThrow(ChainId, v, label);
}

export function ensurePermission(v: unknown): z.infer<typeof Permission> {
  return parseOrThrow(Permission, v, 'permission');
}

export function ensureNetwork(v: unknown): z.infer<typeof NetworkConfig> {
  return parseOrThrow(NetworkConfig, v, 'network config');
}

export function ensureSession(v: unknown): z.infer<typeof SessionState> {
  return parseOrThrow(SessionState, v, 'session state');
}

export function ensureTx(v: unknown): z.infer<typeof TxCommon> {
  return parseOrThrow(TxCommon, v, 'transaction');
}

export function ensureSignRequest(v: unknown): z.infer<typeof SignRequest> {
  return parseOrThrow(SignRequest, v, 'sign request');
}

export function ensureRpcRequest(v: unknown): z.infer<typeof JsonRpcRequest> {
  return parseOrThrow(JsonRpcRequest, v, 'jsonrpc request');
}

export function ensureRpcResponse(v: unknown): z.infer<typeof JsonRpcResponse> {
  return parseOrThrow(JsonRpcResponse, v, 'jsonrpc response');
}

/* ───────────────────────── Bytes helpers & guards ───────────────────────────── */

export function bytesLength(b: z.infer<typeof BytesLike>): number {
  if (typeof b === 'string') {
    // 0x + hex chars
    return (b.length - 2) / 2;
  }
  return b.byteLength;
}

export function assertMaxBytes(
  b: z.infer<typeof BytesLike>,
  maxBytes: number,
  label = 'bytes'
) {
  const len = bytesLength(b);
  if (len > maxBytes) throw new Error(`${label} too large: ${len} > ${maxBytes} bytes`);
}

export function isNonEmptyHex(v: unknown): v is string {
  return typeof v === 'string' && isHexString(v) && v.length > 2;
}

/* ───────────────────────── Safe parse utilities ─────────────────────────────── */

export function safeParse<T>(schema: z.ZodType<T>, data: unknown): { ok: true; value: T } | { ok: false; error: Error } {
  const r = schema.safeParse(data);
  if (r.success) return { ok: true, value: r.data };
  return { ok: false, error: new Error(formatZodError(r.error)) };
}

/** Build a type guard from a Zod schema (for filters/routers) */
export function guardFromSchema<T>(schema: z.ZodType<T>) {
  return (v: unknown): v is T => schema.safeParse(v).success;
}

/* ───────────────────────── Domain-specific quick checks ─────────────────────── */

/** Quick sanity for RPC URLs (http/https only). Use ensureNetwork for full validation. */
export function isHttpUrl(u: string): boolean {
  try {
    const url = new URL(u);
    return url.protocol === 'http:' || url.protocol === 'https:';
  } catch {
    return false;
  }
}

/** Ensure origin-like strings normalize to scheme+host+port (no path/query). */
export function normalizeOriginLike(input: string): string {
  try {
    const url = new URL(input);
    return url.origin;
  } catch {
    // Try to coerce bare host into https://
    const tentative = new URL(`https://${input}`);
    return tentative.origin;
  }
}
