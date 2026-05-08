import { clearTimeoutFn, fetchFn, performanceObj, setTimeoutFn } from '../runtime/env';
import { stringifySafe } from '../core/rpc/safeJson';
import { validateRpcUrl } from './rpcConfig';

interface RpcRequestBody {
  jsonrpc: '2.0';
  id: number;
  method: string;
  params: any[];
}

interface RpcEnvelope {
  result?: any;
  error?: { code?: number; message?: string; data?: unknown };
}

export interface RpcPingInput {
  rpcUrl: string;
  timeoutMs?: number;
}

export interface RpcPingResult {
  ok: boolean;
  chainId: number | null;
  nodeId: string | null;
  latencyMs: number;
  error?: string;
  rawResponse?: unknown;
}

const DEFAULT_TIMEOUT_MS = 20_000;

function nowMs(): number {
  return performanceObj?.now?.() ?? Date.now();
}

function parseNumeric(value: unknown): number | null {
  if (typeof value === 'number' && Number.isFinite(value)) return Math.trunc(value);
  if (typeof value === 'string') {
    const trimmed = value.trim();
    if (!trimmed) return null;
    if (/^0x[0-9a-f]+$/i.test(trimmed) || /^-?\d+$/.test(trimmed)) {
      return Number(BigInt(trimmed));
    }
  }
  if (typeof value === 'bigint') return Number(value);
  return null;
}

function stringifyRpcError(payload: RpcEnvelope): string {
  const code = payload.error?.code;
  const message = payload.error?.message || 'Unknown RPC error';
  return typeof code === 'number' ? `RPC ${code}: ${message}` : message;
}

async function rpcPost(rpcUrl: string, body: RpcRequestBody, timeoutMs: number): Promise<{ envelope: RpcEnvelope; latencyMs: number }> {
  if (typeof fetchFn !== 'function') {
    throw new Error('Fetch API is unavailable in this runtime');
  }
  if (typeof setTimeoutFn !== 'function' || typeof clearTimeoutFn !== 'function') {
    throw new Error('Timer APIs are unavailable in this runtime');
  }

  const controller = new AbortController();
  const start = nowMs();
  const timeout = setTimeoutFn(() => controller.abort(), timeoutMs);

  try {
    const response = await fetchFn(rpcUrl, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: stringifySafe(body),
      signal: controller.signal,
    });

    if (!response.ok) {
      throw new Error(`HTTP ${response.status}: ${response.statusText}`);
    }

    const envelope = (await response.json()) as RpcEnvelope;
    return { envelope, latencyMs: Math.round(nowMs() - start) };
  } finally {
    clearTimeoutFn(timeout);
  }
}

export async function rpcPing(input: RpcPingInput): Promise<RpcPingResult> {
  const validation = validateRpcUrl(input.rpcUrl);
  const timeoutMs = input.timeoutMs ?? DEFAULT_TIMEOUT_MS;

  try {
    const ping = await rpcPost(validation.normalizedUrl, {
      jsonrpc: '2.0',
      id: Date.now(),
      method: 'system.ping',
      params: [],
    }, timeoutMs);

    if (ping.envelope.error) {
      // fallback method for nodes that do not support system.ping
      const head = await rpcPost(validation.normalizedUrl, {
        jsonrpc: '2.0',
        id: Date.now() + 1,
        method: 'chain.getHead',
        params: [],
      }, timeoutMs);

      if (head.envelope.error) {
        return {
          ok: false,
          chainId: null,
          nodeId: null,
          latencyMs: head.latencyMs,
          error: stringifyRpcError(head.envelope),
          rawResponse: head.envelope,
        };
      }

      const headResult = head.envelope.result;
      const chainId = parseNumeric(headResult?.chain_id ?? headResult?.chainId ?? null);
      const nodeId = typeof headResult?.nodeId === 'string' ? headResult.nodeId : null;

      return {
        ok: true,
        chainId,
        nodeId,
        latencyMs: head.latencyMs,
        rawResponse: head.envelope,
      };
    }

    const pingResult = ping.envelope.result;
    const chainId = parseNumeric(pingResult?.chain_id ?? pingResult?.chainId ?? null);
    const nodeId = typeof pingResult?.node_id === 'string'
      ? pingResult.node_id
      : (typeof pingResult?.nodeId === 'string' ? pingResult.nodeId : null);

    return {
      ok: true,
      chainId,
      nodeId,
      latencyMs: ping.latencyMs,
      rawResponse: ping.envelope,
    };
  } catch (error: any) {
    return {
      ok: false,
      chainId: null,
      nodeId: null,
      latencyMs: 0,
      error: error?.name === 'AbortError'
        ? `Request timed out after ${timeoutMs}ms`
        : (error?.message || 'Unknown RPC test error'),
    };
  }
}
