// RPC client for Animica nodes

import { clearTimeoutFn, fetchFn, setTimeoutFn } from '../../runtime/env';
import { submitRawTransactionCompat } from './rawtx_submit';
import { stringifySafe } from './safeJson';

const RPC_TIMEOUT_MS = 10000;

type JsonRpcParams = unknown[] | Record<string, unknown>;

interface JsonRpcRequest {
  jsonrpc: '2.0';
  id: number;
  method: string;
  params: JsonRpcParams;
}

interface RpcAttemptDebug {
  method: string;
  url: string;
  requestBody: string;
  httpStatus?: number;
  rawResponseBody?: string;
  parsedResponse?: unknown;
  error?: string;
  schema?: string;
}

function shouldDebugRpcPayloads(): boolean {
  try {
    const envFlag = (import.meta as any)?.env?.VITE_DEBUG_RPC_PAYLOADS;
    if (envFlag === '1' || envFlag === 'true') return true;
  } catch {}

  try {
    const g = globalThis as any;
    return g.__ANIMICA_DEBUG_RPC_PAYLOADS__ === true || g.__ANIMICA_DEBUG_RPC_PAYLOADS__ === '1';
  } catch {
    return false;
  }
}

let requestIdSeed = 1;


function isTxBroadcastMethod(method: string): boolean {
  return method === 'tx.sendRawTransaction' || method === 'tx_sendRawTransaction' || method === 'tx.submitRawTransaction' || method === 'tx2.sendRawTransaction';
}

function normalizeTxBroadcastParams(method: string, params: JsonRpcParams): JsonRpcParams {
  if (!isTxBroadcastMethod(method)) return params;
  if (Array.isArray(params)) return params;
  const maybeRaw = (params as Record<string, unknown>)?.rawTx;
  if (typeof maybeRaw === 'string') {
    return [maybeRaw];
  }
  return params;
}
export function buildJsonRpcRequest(method: string, params: JsonRpcParams, id: number = requestIdSeed++): JsonRpcRequest {
  return {
    jsonrpc: '2.0',
    id,
    method,
    params: normalizeTxBroadcastParams(method, params),
  };
}

class RpcResponseError extends Error {
  code?: number;
  data?: unknown;

  constructor(message: string, errorObject?: { code?: number; data?: unknown }) {
    super(message);
    this.name = 'RpcResponseError';
    this.code = errorObject?.code;
    this.data = errorObject?.data;
  }
}

function normalizeError(error: unknown): Error {
  if (error instanceof Error) {
    return error;
  }

  if (typeof error === 'string') {
    return new Error(error);
  }

  if (error && typeof error === 'object' && 'message' in error && typeof (error as any).message === 'string') {
    return new Error((error as any).message);
  }

  return new Error(`Unknown error: ${String(error)}`);
}

function toRpcInteger(value: unknown, fieldName: string): number {
  if (typeof value === 'number' && Number.isFinite(value)) {
    return Math.trunc(value);
  }

  if (typeof value === 'bigint') {
    return Number(value);
  }

  if (typeof value === 'string') {
    const trimmed = value.trim();
    if (!trimmed) {
      throw new Error(`RPC ${fieldName} was empty`);
    }

    if (/^0x[0-9a-f]+$/i.test(trimmed)) {
      return Number(BigInt(trimmed));
    }

    if (/^-?\d+$/.test(trimmed)) {
      return Number(BigInt(trimmed));
    }
  }

  throw new Error(`RPC ${fieldName} had invalid numeric value: ${String(value)}`);
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

interface RpcClientOptions {
  timeoutMs?: number;
}

interface RpcCallOptions {
  signal?: AbortSignal;
}

export class RpcClient {
  private urls: string[];
  private currentIndex: number = 0;
  private failedUrls: Set<string> = new Set();
  private timeoutMs: number;
  private requestId: number = 1;
  private lastCallAttempts: RpcAttemptDebug[] = [];

  constructor(urls: string[], options: RpcClientOptions = {}) {
    this.urls = urls;
    this.timeoutMs = options.timeoutMs ?? RPC_TIMEOUT_MS;
  }

  private nextRequestId(): number {
    const id = this.requestId;
    this.requestId += 1;
    if (this.requestId > 1_000_000_000) this.requestId = 1;
    return id;
  }

  getLastCallAttempts(): RpcAttemptDebug[] {
    return [...this.lastCallAttempts];
  }

  async call(method: string, params: JsonRpcParams = [], schema?: string, options: RpcCallOptions = {}): Promise<any> {
    let lastError: Error | null = null;
    const fetchImpl = getFetch();
    const setTimeoutImpl = getSetTimeout();
    const clearTimeoutImpl = getClearTimeout();

    for (let i = 0; i < this.urls.length; i++) {
      const url = this.urls[this.currentIndex];

      if (this.failedUrls.has(url)) {
        this.currentIndex = (this.currentIndex + 1) % this.urls.length;
        continue;
      }

      const controller = new AbortController();
      const externalAbortHandler = () => controller.abort(options.signal?.reason);
      if (options.signal) {
        if (options.signal.aborted) {
          throw new Error('Request aborted before start');
        }
        options.signal.addEventListener('abort', externalAbortHandler, { once: true });
      }
      const timeout = setTimeoutImpl(() => controller.abort(), this.timeoutMs);

      const request = buildJsonRpcRequest(method, params, this.nextRequestId());
      const requestBody = stringifySafe(request);
      const attempt: RpcAttemptDebug = { method, url, requestBody, schema };
      this.lastCallAttempts.push(attempt);

      try {
        const response = await fetchImpl(url, {
          method: 'POST',
          headers: {
            'Content-Type': 'application/json',
          },
          signal: controller.signal,
          body: requestBody,
        });

        attempt.httpStatus = response.status;

        if (!response.ok) {
          throw new Error(`HTTP ${response.status}: ${response.statusText}`);
        }

        let json: any = {};
        if (typeof (response as any).text === 'function') {
          const responseText = await (response as any).text();
          attempt.rawResponseBody = responseText;
          json = responseText ? JSON.parse(responseText) : {};
        } else {
          json = await (response as any).json();
          attempt.rawResponseBody = stringifySafe(json);
        }
        attempt.parsedResponse = json;

        if (json.error) {
          const codePart = typeof json.error.code === 'number' ? ` (code ${json.error.code})` : '';
          const message = typeof json.error.message === 'string' ? json.error.message : 'RPC error';
          const responseError = {
            code: typeof json.error.code === 'number' ? json.error.code : undefined,
            message,
            data: json.error.data,
          };

          // ALWAYS log -32602 (Invalid params) errors with full request details
          const isInvalidParams = json.error.code === -32602;
          if (isInvalidParams || shouldDebugRpcPayloads()) {
            console.error('[wallet-rpc] RPC ERROR', {
              url,
              method,
              fullRequest: request,
              responseError,
              hint: isInvalidParams 
                ? 'Code -32602 means params shape/type mismatch. See apps/wallet-extension/docs/RPC_TRANSACTION_SUBMISSION.md for common causes.'
                : undefined,
            });
          }

          throw new RpcResponseError(`${message}${codePart}`, responseError);
        }

        // Success - clear failed status
        this.failedUrls.delete(url);
        return json.result;
      } catch (error: unknown) {
        attempt.error = error instanceof Error ? error.message : String(error);
        if (error instanceof RpcResponseError) {
          throw error;
        }

        if (shouldDebugRpcPayloads()) {
          console.error('[wallet-rpc] request failed before RPC result', {
            url,
            method,
            request,
            error: error instanceof Error ? { name: error.name, message: error.message } : error,
          });
        }

        const normalizedError = normalizeError(error);
        lastError = normalizedError.name === 'AbortError'
          ? new Error(`Request timed out after ${this.timeoutMs}ms`)
          : normalizedError;
        this.failedUrls.add(url);
        this.currentIndex = (this.currentIndex + 1) % this.urls.length;
      } finally {
        if (options.signal) {
          options.signal.removeEventListener('abort', externalAbortHandler);
        }
        clearTimeoutImpl(timeout);
      }
    }

    throw new Error(
      `All RPC endpoints failed. Last error: ${lastError?.message || 'Unknown'}`,
    );
  }

  async getBalance(address: string, tag: string = 'latest'): Promise<string> {
    return this.call('state.getBalance', [address, tag]);
  }

  async getNonce(address: string, tag: string = 'latest'): Promise<number> {
    const result = await this.call('state.getNonce', [address, tag]);
    return toRpcInteger(result, 'nonce');
  }

  async getPendingNonce(address: string): Promise<number> {
    const attempts: Array<{ method: string; params: JsonRpcParams }> = [
      { method: 'state.getPendingNonce', params: [address] },
      { method: 'state.getNextNonce', params: [address] },
      { method: 'state.getNonce', params: [address, 'pending'] },
      { method: 'state.getNonce', params: [address] },
    ];

    let lastError: Error | null = null;
    for (const attempt of attempts) {
      try {
        const result = await this.call(attempt.method, attempt.params);
        return toRpcInteger(result, 'nonce');
      } catch (error) {
        if (error instanceof RpcResponseError && (error.code === -32601 || error.code === -32602)) {
          lastError = error;
          continue;
        }
        throw error;
      }
    }

    if (lastError) throw lastError;
    throw new Error('Unable to resolve pending nonce');
  }

  async sendRawTransaction(rawTx: string): Promise<string> {
    this.lastCallAttempts = [];

    const out = await submitRawTransactionCompat({
      rpcUrl: this.getActiveUrl(),
      chainId: undefined,
      rawTx,
      timeoutMs: this.timeoutMs,
      jsonRpcId: this.nextRequestId(),
      forceCompat: undefined,
    });

    if (!out.ok || !out.txid) {
      const err = out.error;
      if (typeof err?.code === 'number' || err?.data !== undefined) {
        throw new RpcResponseError(err.message, { code: err.code, data: err.data });
      }
      throw new Error(err?.message ?? 'Failed to submit raw transaction');
    }

    return out.txid;
  }

  async getTransaction(txid: string): Promise<any> {
    return this.call('tx.getTransaction', [txid]);
  }

  async getTransactionStatus(txid: string): Promise<any> {
    return this.call('tx.getTransactionStatus', [txid]);
  }

  async getTransactionReceipt(txid: string): Promise<any> {
    return this.call('tx.getTransactionReceipt', [txid]);
  }

  async getChainId(): Promise<number> {
    const result = await this.call('chain.getChainId', []);
    return toRpcInteger(result, 'chainId');
  }

  async getChainIdentity(): Promise<any> {
    return this.call('chain.getChainIdentity', []);
  }

  async getHead(): Promise<any> {
    return this.call('chain.getHead', []);
  }

  async getBlock(blockId: string | number, full: boolean = false): Promise<any> {
    return this.call('block.getBlock', [blockId, full]);
  }

  async getMempoolStats(): Promise<any> {
    return this.call('tx2.getMempoolStats', []);
  }

  async listMempool(): Promise<string[]> {
    return this.call('mempool.list', []);
  }

  resetFailures(): void {
    this.failedUrls.clear();
  }

  getActiveUrl(): string {
    return this.urls[this.currentIndex];
  }
}

export { RPC_TIMEOUT_MS };
