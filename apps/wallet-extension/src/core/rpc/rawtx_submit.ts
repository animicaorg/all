import { clearTimeoutFn, fetchFn, setTimeoutFn } from '../../runtime/env';
import { stringifySafe } from './safeJson';

const METHOD_CACHE_KEY = 'rawtx_method_cap_cache_v2';
export const FORCE_RAWTX_COMPAT_KEY = 'force_rawtx_compat';

const RETRYABLE_VARIANTS = [
  { method: 'tx.sendRawTransaction', paramsShape: 'array' as const },
  { method: 'tx_sendRawTransaction', paramsShape: 'array' as const },
  { method: 'tx.sendRawTransaction', paramsShape: 'object' as const },
  { method: 'tx_sendRawTransaction', paramsShape: 'object' as const },
  { method: 'tx.sendRawTransaction', paramsShape: 'objectArray' as const },
  { method: 'tx_sendRawTransaction', paramsShape: 'objectArray' as const },
  { method: 'tx.submitRawTransaction', paramsShape: 'array' as const },
  { method: 'tx2.sendRawTransaction', paramsShape: 'array' as const },
] as const;

type Variant = (typeof RETRYABLE_VARIANTS)[number];

type JsonRpcParams = unknown;
type JsonRpcError = { code?: number; message?: string; data?: unknown };
type JsonRpcResponse = { result?: unknown; error?: JsonRpcError };

type CapabilityCache = {
  methods: string[];
  positionalMethods: string[];
  ts: number;
};

export type SubmitRawTransactionResult = {
  ok: boolean;
  txid?: string;
  modeUsed?: string;
  rpcResult?: unknown;
  error?: { code?: number; message: string; data?: unknown };
};

export type SubmitRawTransactionInput = {
  rpcUrl: string;
  chainId?: number;
  rawTx: string;
  timeoutMs: number;
  jsonRpcId?: number;
  forceCompat?: boolean;
};

export async function submitRawTransactionCompat(input: SubmitRawTransactionInput): Promise<SubmitRawTransactionResult> {
  const fetchImpl = getFetch();
  const setTimeoutImpl = getSetTimeout();
  const clearTimeoutImpl = getClearTimeout();

  const normalizedRawTx = normalizeRawTx(input.rawTx);
  const rpcId = input.jsonRpcId ?? 1;

  const capability = await discoverCapabilities(fetchImpl, setTimeoutImpl, clearTimeoutImpl, input.rpcUrl, input.timeoutMs, rpcId);
  const ordered = orderVariants(capability);

  debugLog('submit.start', {
    rpcUrl: input.rpcUrl,
    requestId: rpcId,
    rawTx: summarizeRawTx(normalizedRawTx),
    discoveredMethods: capability?.methods,
  });

  let lastError: SubmitRawTransactionResult['error'];

  for (const variant of ordered) {
    const body = {
      jsonrpc: '2.0' as const,
      id: rpcId,
      method: variant.method,
      params: paramsForVariant(variant, normalizedRawTx),
    };

    const sent = await sendJsonRpc(fetchImpl, setTimeoutImpl, clearTimeoutImpl, {
      rpcUrl: input.rpcUrl,
      timeoutMs: input.timeoutMs,
      body,
    });

    if (sent.transportError) {
      lastError = { message: sent.transportError };
      debugLog('submit.transport_error', {
        rpcUrl: input.rpcUrl,
        requestId: rpcId,
        method: variant.method,
        paramsShape: variant.paramsShape,
        error: sent.transportError,
      });
      continue;
    }

    const rpc = sent.response;
    if (!rpc) {
      lastError = { message: `RPC returned empty response (HTTP ${sent.httpStatus})` };
      continue;
    }

    if (rpc.error) {
      const err = {
        code: rpc.error.code,
        message: formatRpcErrorMessage(rpc.error),
        data: rpc.error.data,
      };
      lastError = err;

      debugLog('submit.rpc_error', {
        rpcUrl: input.rpcUrl,
        requestId: rpcId,
        method: variant.method,
        paramsShape: variant.paramsShape,
        code: rpc.error.code,
        message: rpc.error.message,
      });

      if (isAnimicaTxTerminalError(rpc.error)) {
        return { ok: false, error: decorateAnimicaError(err) };
      }

      if (shouldRetryNextVariant(rpc.error)) {
        continue;
      }

      return { ok: false, error: err };
    }

    const resultTxid = extractTxid(rpc.result);
    if (!resultTxid) {
      return { ok: false, error: { message: 'RPC tx broadcast returned invalid tx hash' } };
    }

    return {
      ok: true,
      txid: resultTxid,
      modeUsed: `${variant.method}:${variant.paramsShape}`,
      rpcResult: rpc.result,
    };
  }

  return {
    ok: false,
    error: lastError ?? { message: 'Failed to submit raw transaction in all compatibility variants' },
  };
}

function paramsForVariant(variant: Variant, rawTx: string): JsonRpcParams {
  if (variant.paramsShape === 'object') return { rawTx };
  if (variant.paramsShape === 'objectArray') return [{ rawTx }];
  return [rawTx];
}

function orderVariants(capability: CapabilityCache | undefined): Variant[] {
  if (!capability) return [...RETRYABLE_VARIANTS];

  const methodSet = new Set(capability.methods);
  const positionalSet = new Set(capability.positionalMethods);

  const scored = RETRYABLE_VARIANTS.map((variant, idx) => {
    let score = 0;
    if (methodSet.has(variant.method)) score += 100;
    if (variant.paramsShape === 'array' && positionalSet.has(variant.method)) score += 50;
    score -= idx;
    return { variant, score };
  }).sort((a, b) => b.score - a.score);

  return scored.map((x) => x.variant);
}

function normalizeRawTx(rawTx: string): string {
  if (typeof rawTx !== 'string') {
    throw new Error('Invalid raw transaction: expected hex string');
  }

  const trimmed = rawTx.trim();
  if (!trimmed.startsWith('0x') && !trimmed.startsWith('0X')) {
    throw new Error('Invalid raw transaction: must start with 0x');
  }

  let hex = trimmed.slice(2);
  if (!/^[0-9a-f]*$/i.test(hex)) {
    throw new Error('Invalid raw transaction: must be hex');
  }

  if (hex.length === 0 || /^0+$/i.test(hex)) {
    throw new Error('Invalid raw transaction: payload too short');
  }

  if (hex.length % 2 !== 0) {
    hex = `0${hex}`;
  }

  if (hex.length <= 2) {
    throw new Error('Invalid raw transaction: payload too short');
  }

  return `0x${hex.toLowerCase()}`;
}

function formatRpcErrorMessage(error: JsonRpcError): string {
  const base = error.message ?? 'RPC error';
  return typeof error.code === 'number' ? `${base} (code ${error.code})` : base;
}

function shouldRetryNextVariant(error: JsonRpcError): boolean {
  return error.code === -32602 || error.code === -32601;
}

function isAnimicaTxTerminalError(error: JsonRpcError): boolean {
  return error.code === -32010 || error.code === -32011 || error.code === -32012;
}

function decorateAnimicaError(error: { code?: number; message: string; data?: unknown }): { code?: number; message: string; data?: unknown } {
  if (error.code === -32011) {
    const data = error.data as Record<string, unknown> | undefined;
    const expected = data?.expectedChainId ?? data?.expected_chain_id;
    const actual = data?.detectedChainId ?? data?.detected_chain_id;
    return {
      ...error,
      message: `ChainId mismatch. Expected ${String(expected ?? 'unknown')}, detected ${String(actual ?? 'unknown')}. Switch network and retry.`,
    };
  }

  if (error.code === -32012) {
    const data = error.data as Record<string, unknown> | undefined;
    // data.fee can come back from the node carrying bigint-valued fields (gas
    // price, max fee, etc.). Plain JSON.stringify would throw and mask the
    // real `-32012 Fee too low` error with `Do not know how to serialize a
    // BigInt`. Use stringifySafe so the operator sees the intended message.
    const feeSummary = data?.fee ? ` Current fee fields: ${stringifySafe(data.fee)}` : '';
    return {
      ...error,
      message: `Fee too low. Increase fee and retry.${feeSummary}`,
    };
  }

  if (error.code === -32010) {
    return {
      ...error,
      message: 'Transaction decode failed. Verify signed transaction bytes and retry.',
    };
  }

  return error;
}

function extractTxid(result: unknown): string | undefined {
  if (typeof result === 'string' && result.startsWith('0x')) return result;
  if (!result || typeof result !== 'object') return undefined;
  const obj = result as Record<string, unknown>;
  for (const key of ['txid', 'hash', 'txHash', 'transactionHash']) {
    const value = obj[key];
    if (typeof value === 'string' && value.startsWith('0x')) return value;
  }
  return undefined;
}

function summarizeRawTx(rawTx: string): string {
  return `${rawTx.slice(0, 10)}... (len=${rawTx.length})`;
}

async function discoverCapabilities(
  fetchImpl: typeof fetch,
  setTimeoutImpl: typeof setTimeout,
  clearTimeoutImpl: typeof clearTimeout,
  rpcUrl: string,
  timeoutMs: number,
  rpcId: number,
): Promise<CapabilityCache | undefined> {
  const key = `unknown::${rpcUrl}`;
  const cache = await readCapabilityCache(key);
  if (cache) return cache;

  const discover = await sendJsonRpc(fetchImpl, setTimeoutImpl, clearTimeoutImpl, {
    rpcUrl,
    timeoutMs,
    body: { jsonrpc: '2.0', id: rpcId, method: 'rpc.discover', params: [] },
  });

  let capability: CapabilityCache | undefined;

  if (discover.response?.result) {
    capability = parseDiscoverResult(discover.response.result);
  }

  if (!capability) {
    const listMethods = await sendJsonRpc(fetchImpl, setTimeoutImpl, clearTimeoutImpl, {
      rpcUrl,
      timeoutMs,
      body: { jsonrpc: '2.0', id: rpcId, method: 'rpc.listMethods', params: [] },
    });
    if (listMethods.response?.result) {
      capability = parseListMethodsResult(listMethods.response.result);
    }
  }

  if (capability) {
    await writeCapabilityCache(key, capability);
  }

  return capability;
}

function parseDiscoverResult(result: unknown): CapabilityCache | undefined {
  if (!result || typeof result !== 'object') return undefined;
  const methodsField = (result as any).methods;
  if (!Array.isArray(methodsField)) return undefined;

  const methods: string[] = [];
  const positionalMethods: string[] = [];

  for (const entry of methodsField) {
    const name = typeof entry?.name === 'string' ? entry.name : undefined;
    if (!name) continue;
    methods.push(name);
    if (Array.isArray(entry?.params)) positionalMethods.push(name);
  }

  return { methods, positionalMethods, ts: Date.now() };
}

function parseListMethodsResult(result: unknown): CapabilityCache | undefined {
  if (!Array.isArray(result)) return undefined;
  const methods = result.filter((x): x is string => typeof x === 'string');
  return { methods, positionalMethods: [], ts: Date.now() };
}

async function sendJsonRpc(
  fetchImpl: typeof fetch,
  setTimeoutImpl: typeof setTimeout,
  clearTimeoutImpl: typeof clearTimeout,
  input: { rpcUrl: string; timeoutMs: number; body: Record<string, unknown> },
): Promise<{ response?: JsonRpcResponse; httpStatus?: number; transportError?: string }> {
  const controller = new AbortController();
  const timeout = setTimeoutImpl(() => controller.abort(), input.timeoutMs);
  try {
    const response = await fetchImpl(input.rpcUrl, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: stringifySafe(input.body),
      signal: controller.signal,
    });

    const text = typeof (response as any).text === 'function'
      ? await (response as any).text()
      : stringifySafe(await (response as any).json());

    let parsed: JsonRpcResponse | undefined;
    try {
      parsed = text ? (JSON.parse(text) as JsonRpcResponse) : undefined;
    } catch {
      parsed = undefined;
    }

    return { response: parsed, httpStatus: response.status };
  } catch (error) {
    return { transportError: error instanceof Error ? error.message : String(error) };
  } finally {
    clearTimeoutImpl(timeout);
  }
}

async function readCapabilityCache(cacheKey: string): Promise<CapabilityCache | undefined> {
  const value = await getStorageValue(METHOD_CACHE_KEY);
  if (!value || typeof value !== 'object') return undefined;
  const map = value as Record<string, CapabilityCache | undefined>;
  return map[cacheKey];
}

async function writeCapabilityCache(cacheKey: string, cap: CapabilityCache): Promise<void> {
  const value = await getStorageValue(METHOD_CACHE_KEY);
  const map = value && typeof value === 'object' ? (value as Record<string, CapabilityCache>) : {};
  map[cacheKey] = cap;
  await setStorageValue(METHOD_CACHE_KEY, map);
}

const memoryStorage = new Map<string, unknown>();

async function getStorageValue(key: string): Promise<unknown> {
  const chromeStorage = (globalThis as any)?.chrome?.storage?.local;
  if (chromeStorage?.get) {
    const result = await chromeStorage.get([key]);
    return result?.[key];
  }
  return memoryStorage.get(key);
}

async function setStorageValue(key: string, value: unknown): Promise<void> {
  const chromeStorage = (globalThis as any)?.chrome?.storage?.local;
  if (chromeStorage?.set) {
    await chromeStorage.set({ [key]: value });
    return;
  }
  memoryStorage.set(key, value);
}

function getFetch(): typeof fetch {
  if (typeof fetchFn !== 'function') {
    throw new Error('Fetch API is unavailable in this runtime');
  }
  return fetchFn;
}

function getSetTimeout(): typeof setTimeout {
  if (typeof setTimeoutFn !== 'function') {
    throw new Error('setTimeout is unavailable in this runtime');
  }
  return setTimeoutFn;
}

function getClearTimeout(): typeof clearTimeout {
  if (typeof clearTimeoutFn !== 'function') {
    throw new Error('clearTimeout is unavailable in this runtime');
  }
  return clearTimeoutFn;
}

function debugLog(event: string, data: Record<string, unknown>): void {
  const enabled = (globalThis as any).__ANIMICA_DEBUG_TX_BROADCAST__ === true
    || (globalThis as any).__ANIMICA_DEBUG_TX_BROADCAST__ === '1'
    || (import.meta as any)?.env?.VITE_DEBUG_TX_BROADCAST === '1';
  if (!enabled) return;
  console.debug(`[wallet-rpc][rawtx] ${event}`, data);
}
