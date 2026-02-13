import { bytesToHex, hexToBytes, isHex, normalizeHex } from '../utils/bytes';
import { sha3_256 } from '../utils/hash';

const MODE_CACHE_KEY = 'studio.rawtx.compat.mode.v1';
const FORCE_COMPAT_KEY = 'force_rawtx_compat';
const MAX_RETRIES_PER_MODE = 3;

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

export type SubmitRawTxResult = {
  ok: boolean;
  txid?: string;
  modeUsed?: RawTxMode;
  rpcResult?: unknown;
  error?: { code?: number; message: string; data?: unknown };
};

type JsonRpcError = { code?: number; message?: string; data?: unknown };
type JsonRpcResponse = { result?: unknown; error?: JsonRpcError };

type RpcSendResult = {
  ok: boolean;
  status: number;
  body?: JsonRpcResponse;
  transportError?: unknown;
  retriableTransportFailure?: boolean;
};

export type RawTxSubmitOptions = {
  rpcUrl: string;
  chainId?: number;
  rawTx: string | Uint8Array;
  headers?: Record<string, string>;
  timeoutMs?: number;
  maxRetriesPerMode?: number;
  forceCompat?: boolean;
  logger?: Pick<Console, 'debug' | 'info' | 'warn' | 'error'>;
  jsonRpcId?: number;
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

export async function submitRawTxCompat(opts: RawTxSubmitOptions): Promise<SubmitRawTxResult> {
  const logger = opts.logger ?? console;
  const rpcId = opts.jsonRpcId ?? 1;
  const timeoutMs = opts.timeoutMs ?? 12_000;
  const retries = Math.max(1, opts.maxRetriesPerMode ?? MAX_RETRIES_PER_MODE);

  const rawBytes = typeof opts.rawTx === 'string' ? parseRawTxToBytes(opts.rawTx) : new Uint8Array(opts.rawTx);
  const hexTx = normalizeHex(bytesToHex(rawBytes));
  const b64Tx = toBase64(rawBytes);
  const deterministicTxid = bytesToHex(sha3_256(rawBytes));

  const cacheKey = modeCacheIndex(opts.chainId, opts.rpcUrl);
  const cachedMode = readCachedMode(cacheKey);
  let forceCompat = opts.forceCompat ?? readForceCompatFlag();

  logger.info('[rawtx] submit start', {
    rpcUrl: opts.rpcUrl,
    chainId: opts.chainId,
    forceCompat,
    cachedMode,
    txFingerprint: maskTx(hexTx),
    txid: deterministicTxid,
  });

  const baseOrder: RawTxMode[] = forceCompat ? [...HEX_MODES, ...B64_MODES] : [HEX_MODES[0]];
  const modeOrder = cachedMode ? [cachedMode, ...baseOrder.filter((m) => m !== cachedMode)] : baseOrder;

  let invalidHexCount = 0;
  let transportAmbiguous = false;
  let lastError: SubmitRawTxResult['error'];

  for (const mode of modeOrder) {
    if (B64_MODES.includes(mode) && invalidHexCount < HEX_MODES.length) continue;

    const params = paramsForMode(mode, hexTx, b64Tx);

    for (let attempt = 1; attempt <= retries; attempt++) {
      const payload = { jsonrpc: '2.0' as const, id: rpcId, method: 'tx.sendRawTransaction', params };
      const res = await sendRpc(opts.rpcUrl, payload, opts.headers, timeoutMs);

      logger.debug('[rawtx] send attempt', {
        mode,
        attempt,
        status: res.status,
        txid: deterministicTxid,
        txFingerprint: maskTx(hexTx),
        response: summarizeResponse(res.body),
        transportError: !!res.transportError,
      });

      if (res.transportError || res.retriableTransportFailure) {
        transportAmbiguous = true;
        if (attempt < retries) {
          await sleep(backoffWithJitter(attempt));
          continue;
        }
        break;
      }

      const rpc = res.body;
      if (!rpc) {
        lastError = { message: `RPC returned empty/non-JSON body (HTTP ${res.status})` };
        break;
      }

      if (rpc.error) {
        const err = rpc.error;
        lastError = { code: err.code, message: err.message ?? 'RPC error', data: err.data };

        if (isMethodNotFound(err)) {
          return { ok: false, txid: deterministicTxid, error: { code: err.code, message: 'RPC does not support tx submission', data: err.data } };
        }
        if (isAlreadyKnown(err)) {
          writeCachedMode(cacheKey, mode);
          return { ok: true, txid: deterministicTxid, modeUsed: mode, rpcResult: rpc.result ?? err };
        }
        if (isInvalidParams(err)) {
          if (HEX_MODES.includes(mode)) invalidHexCount += 1;
          if (cachedMode === mode) clearCachedMode(cacheKey);
          break;
        }
        if (isRetriableRpcError(err) && attempt < retries) {
          await sleep(backoffWithJitter(attempt));
          continue;
        }
        break;
      }

      const txid = extractTxid(rpc.result) ?? deterministicTxid;
      writeCachedMode(cacheKey, mode);
      return { ok: true, txid, modeUsed: mode, rpcResult: rpc.result };
    }

    if (!forceCompat && lastError?.code === -32602) {
      forceCompat = true;
      const expandedModes = [...HEX_MODES, ...B64_MODES].filter((m) => !modeOrder.includes(m));
      modeOrder.push(...expandedModes);
    }
  }

  if (transportAmbiguous) {
    const post = await postCheckByTxid(opts.rpcUrl, deterministicTxid, opts.headers, timeoutMs, logger);
    if (post.ok) return post;
  }

  return {
    ok: false,
    txid: deterministicTxid,
    error: lastError ?? { message: 'Failed to submit raw transaction in all compatibility modes' },
  };
}

async function postCheckByTxid(
  rpcUrl: string,
  txid: string,
  headers: Record<string, string> | undefined,
  timeoutMs: number,
  logger: Pick<Console, 'debug' | 'warn'>,
): Promise<SubmitRawTxResult> {
  const methods = ['tx.getTransactionByHash', 'tx.getTransaction', 'tx.getReceipt', 'tx.getTransactionReceipt'];
  for (const method of methods) {
    const response = await sendRpc(rpcUrl, { jsonrpc: '2.0', id: 1, method, params: [txid] }, headers, timeoutMs);
    if (response.transportError || response.retriableTransportFailure || response.body?.error) continue;
    if (response.body?.result) {
      logger.warn('[rawtx] transport ambiguity resolved via post-check', { method, txid });
      return { ok: true, txid, modeUsed: 'array:string:hex', rpcResult: response.body.result };
    }
  }
  return { ok: false, error: { message: 'post-check could not verify transaction acceptance' } };
}

function paramsForMode(mode: RawTxMode, hexTx: string, b64Tx: string): unknown {
  switch (mode) {
    case 'array:string:hex': return [hexTx];
    case 'array:obj:rawTx:hex': return [{ rawTx: hexTx }];
    case 'array:obj:raw_tx:hex': return [{ raw_tx: hexTx }];
    case 'array:obj:tx:hex': return [{ tx: hexTx }];
    case 'obj:rawTx:hex': return { rawTx: hexTx };
    case 'obj:raw_tx:hex': return { raw_tx: hexTx };
    case 'obj:tx:hex': return { tx: hexTx };
    case 'array:obj:rawTxB64:b64': return [{ rawTxB64: b64Tx }];
    case 'array:string:b64': return [b64Tx];
  }
}

function parseRawTxToBytes(raw: string): Uint8Array {
  const trimmed = raw.trim();
  if (isHex(trimmed) || /^[0-9a-fA-F]+$/.test(trimmed)) return hexToBytes(trimmed);
  throw new Error('Expected raw transaction as hex string');
}

function summarizeResponse(body?: JsonRpcResponse): unknown {
  if (!body) return null;
  return {
    hasResult: Object.prototype.hasOwnProperty.call(body, 'result'),
    errorCode: body.error?.code,
    errorMessage: body.error?.message,
  };
}

function maskTx(hexTx: string): string {
  if (hexTx.length <= 20) return hexTx;
  return `${hexTx.slice(0, 12)}…${hexTx.slice(-8)}`;
}

function isInvalidParams(err: JsonRpcError): boolean {
  return err.code === -32602 || /invalid params?/i.test(err.message ?? '');
}

function isMethodNotFound(err: JsonRpcError): boolean {
  return err.code === -32601 || /method not found|unsupported method|does not exist/i.test(err.message ?? '');
}

function isAlreadyKnown(err: JsonRpcError): boolean {
  return /already known|already in mempool|known transaction|duplicate/i.test(err.message ?? '');
}

function isRetriableRpcError(err: JsonRpcError): boolean {
  const msg = (err.message ?? '').toLowerCase();
  return msg.includes('timeout') || msg.includes('temporar') || msg.includes('bad gateway') || msg.includes('gateway timeout');
}

function extractTxid(result: unknown): string | undefined {
  if (typeof result === 'string') return result;
  if (!result || typeof result !== 'object') return undefined;
  const r = result as Record<string, unknown>;
  for (const key of ['txid', 'hash', 'txHash', 'transactionHash']) {
    const value = r[key];
    if (typeof value === 'string') return value;
  }
  return undefined;
}

async function sendRpc(
  rpcUrl: string,
  payload: unknown,
  headers: Record<string, string> | undefined,
  timeoutMs: number,
): Promise<RpcSendResult> {
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), timeoutMs);
  try {
    const response = await fetch(rpcUrl, {
      method: 'POST',
      headers: { 'content-type': 'application/json', ...(headers ?? {}) },
      body: JSON.stringify(payload),
      signal: controller.signal,
    });

    const text = await response.text();
    let body: JsonRpcResponse | undefined;
    try {
      body = text ? (JSON.parse(text) as JsonRpcResponse) : undefined;
    } catch {
      body = undefined;
    }

    const retriableTransportFailure = response.status === 502 || response.status === 504 || response.status === 408;
    const ok = response.ok && !!body && !body.error;
    return { ok, status: response.status, body, retriableTransportFailure };
  } catch (transportError) {
    return { ok: false, status: 0, transportError, retriableTransportFailure: true };
  } finally {
    clearTimeout(timeout);
  }
}

function modeCacheIndex(chainId: number | undefined, rpcUrl: string): string {
  return `${chainId ?? 'unknown'}::${rpcUrl}`;
}

function readForceCompatFlag(): boolean {
  const fromEnv = (import.meta as any)?.env?.VITE_FORCE_RAWTX_COMPAT;
  if (fromEnv === '1' || fromEnv === 'true') return true;
  try {
    const value = localStorage.getItem(FORCE_COMPAT_KEY);
    return value === '1' || value === 'true';
  } catch {
    return false;
  }
}

function readCachedMode(cacheKey: string): RawTxMode | undefined {
  try {
    const raw = localStorage.getItem(MODE_CACHE_KEY);
    if (!raw) return undefined;
    const parsed = JSON.parse(raw) as Record<string, { mode: RawTxMode; ts: number }>;
    return parsed[cacheKey]?.mode;
  } catch {
    return undefined;
  }
}

function writeCachedMode(cacheKey: string, mode: RawTxMode): void {
  try {
    const raw = localStorage.getItem(MODE_CACHE_KEY);
    const map = raw ? (JSON.parse(raw) as Record<string, { mode: RawTxMode; ts: number }>) : {};
    map[cacheKey] = { mode, ts: Date.now() };
    localStorage.setItem(MODE_CACHE_KEY, JSON.stringify(map));
  } catch {
    // noop
  }
}

function clearCachedMode(cacheKey: string): void {
  try {
    const raw = localStorage.getItem(MODE_CACHE_KEY);
    if (!raw) return;
    const map = JSON.parse(raw) as Record<string, { mode: RawTxMode; ts: number }>;
    delete map[cacheKey];
    localStorage.setItem(MODE_CACHE_KEY, JSON.stringify(map));
  } catch {
    // noop
  }
}

function backoffWithJitter(attempt: number): number {
  const base = Math.min(4000, 250 * (2 ** (attempt - 1)));
  const jitter = Math.floor(Math.random() * 125);
  return base + jitter;
}

function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

function toBase64(bytes: Uint8Array): string {
  if (typeof Buffer !== 'undefined') return Buffer.from(bytes).toString('base64');
  let binary = '';
  for (let i = 0; i < bytes.length; i++) binary += String.fromCharCode(bytes[i] ?? 0);
  return btoa(binary);
}
