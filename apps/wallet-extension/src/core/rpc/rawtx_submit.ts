import { bytesToHexRaw, hexToBytes } from '../crypto/convert';
import { sha3Hash } from '../crypto/pq';
import { clearTimeoutFn, fetchFn, setTimeoutFn } from '../../runtime/env';

const MODE_CACHE_KEY = 'rawtx_compat_mode_cache_v1';
export const FORCE_RAWTX_COMPAT_KEY = 'force_rawtx_compat';
const MAX_RETRIES_PER_MODE = 3;

type JsonRpcParams = unknown[] | Record<string, unknown>;

type JsonRpcError = { code?: number; message?: string; data?: unknown };
type JsonRpcResponse = { result?: unknown; error?: JsonRpcError };

export type RawTxMode =
  | 'array:string:hex'
  | 'array:obj:rawTx:hex'
  | 'array:obj:raw_tx:hex'
  | 'array:obj:tx:hex'
  | 'obj:rawTx:hex'
  | 'obj:raw_tx:hex'
  | 'obj:tx:hex'
  | 'array:obj:rawTxB64:b64'
  | 'array:string:b64';

export type SubmitRawTransactionResult = {
  ok: boolean;
  txid?: string;
  modeUsed?: RawTxMode;
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
  maxRetriesPerMode?: number;
};

const HEX_MODES: RawTxMode[] = [
  'array:string:hex',
  'array:obj:rawTx:hex',
  'array:obj:raw_tx:hex',
  'array:obj:tx:hex',
  'obj:rawTx:hex',
  'obj:raw_tx:hex',
  'obj:tx:hex',
];

const B64_MODES: RawTxMode[] = ['array:obj:rawTxB64:b64', 'array:string:b64'];

export async function submitRawTransactionCompat(input: SubmitRawTransactionInput): Promise<SubmitRawTransactionResult> {
  const fetchImpl = getFetch();
  const setTimeoutImpl = getSetTimeout();
  const clearTimeoutImpl = getClearTimeout();

  const normalizedRawTx = normalizeRawTx(input.rawTx);
  const rawBytes = hexToBytes(normalizedRawTx, 'rawTx');
  const txid = '0x' + bytesToHexRaw(sha3Hash(rawBytes));
  const rawTxB64 = bytesToBase64(rawBytes);
  const rpcId = input.jsonRpcId ?? 1;
  const maxRetries = Math.max(1, input.maxRetriesPerMode ?? MAX_RETRIES_PER_MODE);

  const cacheKey = `${input.chainId ?? 'unknown'}::${input.rpcUrl}`;
  const forced = input.forceCompat ?? (await readForceCompatFlag());
  const cachedMode = await readModeCache(cacheKey);

  const modeOrder = buildModeOrder(cachedMode, forced);
  let invalidHexCount = 0;
  let lastError: SubmitRawTransactionResult['error'];
  let sawAmbiguousFailure = false;

  debugLog('submit.start', {
    rpcUrl: input.rpcUrl,
    chainId: input.chainId,
    cachedMode,
    forced,
    txid,
    tx: summarizeRawTx(normalizedRawTx),
  });

  for (const mode of modeOrder) {
    if (B64_MODES.includes(mode) && invalidHexCount < HEX_MODES.length) continue;

    const params = paramsForMode(mode, normalizedRawTx, rawTxB64);

    for (let attempt = 1; attempt <= maxRetries; attempt++) {
      const body = {
        jsonrpc: '2.0' as const,
        id: rpcId,
        method: 'tx.sendRawTransaction',
        params,
      };

      const sent = await sendJsonRpc(fetchImpl, setTimeoutImpl, clearTimeoutImpl, {
        rpcUrl: input.rpcUrl,
        timeoutMs: input.timeoutMs,
        body,
      });

      debugLog('submit.attempt', {
        mode,
        attempt,
        httpStatus: sent.httpStatus,
        txid,
        tx: summarizeRawTx(normalizedRawTx),
        transportError: sent.transportError,
        rpcError: sent.response?.error,
      });

      if (sent.transportError) {
        sawAmbiguousFailure = true;
        if (attempt < maxRetries) {
          await wait(backoffMs(attempt));
          continue;
        }
        break;
      }

      const rpc = sent.response;
      if (!rpc) {
        lastError = { message: `RPC returned empty response (HTTP ${sent.httpStatus})` };
        break;
      }

      if (rpc.error) {
        lastError = {
          code: rpc.error.code,
          message: formatRpcErrorMessage(rpc.error),
          data: rpc.error.data,
        };

        if (isMethodNotFound(rpc.error)) {
          return {
            ok: false,
            txid,
            error: {
              code: rpc.error.code,
              message: 'RPC does not support tx submission',
              data: rpc.error.data,
            },
          };
        }

        if (isAlreadyKnown(rpc.error)) {
          await writeModeCache(cacheKey, mode);
          return { ok: true, txid, modeUsed: mode, rpcResult: rpc.result ?? rpc.error };
        }

        if (isInvalidParams(rpc.error)) {
          if (HEX_MODES.includes(mode)) invalidHexCount += 1;
          if (cachedMode === mode) await clearModeCache(cacheKey);
          break;
        }

        if (isRetriableRpcError(rpc.error) && attempt < maxRetries) {
          sawAmbiguousFailure = true;
          await wait(backoffMs(attempt));
          continue;
        }

        break;
      }

      const resultTxid = extractTxid(rpc.result) ?? txid;
      await writeModeCache(cacheKey, mode);
      return { ok: true, txid: resultTxid, modeUsed: mode, rpcResult: rpc.result };
    }
  }

  if (sawAmbiguousFailure) {
    const postCheck = await postCheckSubmittedTx(
      fetchImpl,
      setTimeoutImpl,
      clearTimeoutImpl,
      input.rpcUrl,
      input.timeoutMs,
      txid,
      rpcId,
    );
    if (postCheck.ok) return postCheck;
  }

  return {
    ok: false,
    txid,
    error: lastError ?? { message: 'Failed to submit raw transaction in all compatibility modes' },
  };
}

function paramsForMode(mode: RawTxMode, hexRawTx: string, rawTxB64: string): JsonRpcParams {
  switch (mode) {
    case 'array:string:hex': return [hexRawTx];
    case 'array:obj:rawTx:hex': return [{ rawTx: hexRawTx }];
    case 'array:obj:raw_tx:hex': return [{ raw_tx: hexRawTx }];
    case 'array:obj:tx:hex': return [{ tx: hexRawTx }];
    case 'obj:rawTx:hex': return { rawTx: hexRawTx };
    case 'obj:raw_tx:hex': return { raw_tx: hexRawTx };
    case 'obj:tx:hex': return { tx: hexRawTx };
    case 'array:obj:rawTxB64:b64': return [{ rawTxB64: rawTxB64 }];
    case 'array:string:b64': return [rawTxB64];
  }
}

function buildModeOrder(cachedMode: RawTxMode | undefined, forced: boolean): RawTxMode[] {
  const base = forced ? [...HEX_MODES, ...B64_MODES] : [...HEX_MODES, ...B64_MODES];
  if (!cachedMode) return base;
  return [cachedMode, ...base.filter((x) => x !== cachedMode)];
}

function normalizeRawTx(rawTx: string): string {
  if (typeof rawTx !== 'string') {
    throw new Error('Invalid tx.sendRawTransaction rawTx: expected hex string');
  }
  const cleaned = rawTx.startsWith('0x') || rawTx.startsWith('0X') ? rawTx.slice(2) : rawTx;
  if (!/^[0-9a-f]+$/i.test(cleaned)) {
    throw new Error('Invalid tx.sendRawTransaction rawTx: expected hex string');
  }
  if (cleaned.length % 2 !== 0) {
    throw new Error('Invalid tx.sendRawTransaction rawTx: hex length must be even');
  }
  return `0x${cleaned.toLowerCase()}`;
}


function formatRpcErrorMessage(error: JsonRpcError): string {
  const base = error.message ?? 'RPC error';
  return typeof error.code === 'number' ? `${base} (code ${error.code})` : base;
}

function isInvalidParams(error: JsonRpcError): boolean {
  return error.code === -32602 || /invalid params?/i.test(error.message ?? '');
}

function isMethodNotFound(error: JsonRpcError): boolean {
  return error.code === -32601 || /method not found|unsupported method|does not exist/i.test(error.message ?? '');
}

function isAlreadyKnown(error: JsonRpcError): boolean {
  return /already known|already in mempool|known transaction|duplicate/i.test(error.message ?? '');
}

function isRetriableRpcError(error: JsonRpcError): boolean {
  const msg = (error.message ?? '').toLowerCase();
  return msg.includes('timeout') || msg.includes('temporar') || msg.includes('bad gateway') || msg.includes('gateway timeout');
}

function extractTxid(result: unknown): string | undefined {
  if (typeof result === 'string') return result;
  if (!result || typeof result !== 'object') return undefined;
  const obj = result as Record<string, unknown>;
  for (const key of ['txid', 'hash', 'txHash', 'transactionHash']) {
    const value = obj[key];
    if (typeof value === 'string' && value.length > 0) return value;
  }
  return undefined;
}

function summarizeRawTx(rawTx: string): Record<string, unknown> {
  const clean = rawTx.startsWith('0x') ? rawTx.slice(2) : rawTx;
  return {
    len: clean.length,
    prefix: `${rawTx.slice(0, 14)}…`,
    suffix: `…${rawTx.slice(-10)}`,
  };
}

async function postCheckSubmittedTx(
  fetchImpl: typeof fetch,
  setTimeoutImpl: typeof setTimeout,
  clearTimeoutImpl: typeof clearTimeout,
  rpcUrl: string,
  timeoutMs: number,
  txid: string,
  rpcId: number,
): Promise<SubmitRawTransactionResult> {
  const methods = ['tx.getTransactionByHash', 'tx.getTransaction', 'tx.getReceipt', 'tx.getTransactionReceipt'];
  for (const method of methods) {
    const sent = await sendJsonRpc(fetchImpl, setTimeoutImpl, clearTimeoutImpl, {
      rpcUrl,
      timeoutMs,
      body: {
        jsonrpc: '2.0',
        id: rpcId,
        method,
        params: [txid],
      },
    });
    if (sent.transportError || sent.response?.error) continue;
    if (sent.response?.result) {
      debugLog('submit.post_check.hit', { method, txid });
      return { ok: true, txid, modeUsed: 'array:string:hex', rpcResult: sent.response.result };
    }
  }
  return { ok: false, error: { message: 'post-check could not confirm tx acceptance' } };
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
      body: JSON.stringify(input.body),
      signal: controller.signal,
    });

    const text = typeof (response as any).text === 'function'
      ? await (response as any).text()
      : JSON.stringify(await (response as any).json());

    let parsed: JsonRpcResponse | undefined;
    try {
      parsed = text ? (JSON.parse(text) as JsonRpcResponse) : undefined;
    } catch {
      parsed = undefined;
    }

    if (!response.ok && [408, 429, 502, 503, 504].includes(response.status)) {
      return { httpStatus: response.status, transportError: `HTTP ${response.status}` };
    }

    return { response: parsed, httpStatus: response.status };
  } catch (error) {
    return { transportError: error instanceof Error ? error.message : String(error) };
  } finally {
    clearTimeoutImpl(timeout);
  }
}

async function readForceCompatFlag(): Promise<boolean> {
  const stored = await getStorageValue(FORCE_RAWTX_COMPAT_KEY);
  return stored === true || stored === '1' || stored === 'true';
}

async function readModeCache(cacheKey: string): Promise<RawTxMode | undefined> {
  const value = await getStorageValue(MODE_CACHE_KEY);
  if (!value || typeof value !== 'object') return undefined;
  const map = value as Record<string, { mode?: RawTxMode }>;
  return map[cacheKey]?.mode;
}

async function writeModeCache(cacheKey: string, mode: RawTxMode): Promise<void> {
  const value = await getStorageValue(MODE_CACHE_KEY);
  const map = value && typeof value === 'object' ? (value as Record<string, { mode: RawTxMode; ts: number }>) : {};
  map[cacheKey] = { mode, ts: Date.now() };
  await setStorageValue(MODE_CACHE_KEY, map);
}

async function clearModeCache(cacheKey: string): Promise<void> {
  const value = await getStorageValue(MODE_CACHE_KEY);
  if (!value || typeof value !== 'object') return;
  const map = value as Record<string, unknown>;
  delete map[cacheKey];
  await setStorageValue(MODE_CACHE_KEY, map);
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

function backoffMs(attempt: number): number {
  const isTest = typeof (globalThis as any).__vitest_worker__ !== 'undefined' || (import.meta as any)?.env?.MODE === 'test';
  if (isTest) return 1;
  const base = Math.min(4000, 250 * 2 ** Math.max(0, attempt - 1));
  const jitter = Math.floor(Math.random() * 150);
  return base + jitter;
}

function wait(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

function bytesToBase64(bytes: Uint8Array): string {
  if (typeof Buffer !== 'undefined') {
    return Buffer.from(bytes).toString('base64');
  }
  let binary = '';
  for (let i = 0; i < bytes.length; i++) {
    binary += String.fromCharCode(bytes[i]);
  }
  return btoa(binary);
}

function debugLog(event: string, data: Record<string, unknown>): void {
  console.debug(`[wallet-rpc][rawtx] ${event}`, data);
}
