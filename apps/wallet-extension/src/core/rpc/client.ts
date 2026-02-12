// RPC client for Animica nodes

import { clearTimeoutFn, fetchFn, setTimeoutFn } from '../../runtime/env';

const RPC_TIMEOUT_MS = 10000;

type JsonRpcParams = unknown[] | Record<string, unknown>;

interface JsonRpcRequest {
  jsonrpc: '2.0';
  id: number;
  method: string;
  params: JsonRpcParams;
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

export function buildJsonRpcRequest(method: string, params: JsonRpcParams, id: number = Date.now()): JsonRpcRequest {
  return {
    jsonrpc: '2.0',
    id,
    method,
    params,
  };
}

function validateSendRawTransactionParams(params: JsonRpcParams): asserts params is { rawTx: string } {
  if (!params || typeof params !== 'object' || Array.isArray(params)) {
    throw new Error(
      `Invalid tx.sendRawTransaction params: expected object { rawTx: string }, got ${Array.isArray(params) ? 'array' : typeof params}`,
    );
  }

  const rawTx = (params as Record<string, unknown>).rawTx;
  if (typeof rawTx !== 'string' || !/^0x[0-9a-f]+$/i.test(rawTx)) {
    throw new Error(
      'Invalid tx.sendRawTransaction params.rawTx: expected 0x-prefixed hex string in object form { rawTx: "0x..." }',
    );
  }
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

export class RpcClient {
  private urls: string[];
  private currentIndex: number = 0;
  private failedUrls: Set<string> = new Set();
  private timeoutMs: number;

  constructor(urls: string[], options: RpcClientOptions = {}) {
    this.urls = urls;
    this.timeoutMs = options.timeoutMs ?? RPC_TIMEOUT_MS;
  }

  async call(method: string, params: JsonRpcParams = []): Promise<any> {
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
      const timeout = setTimeoutImpl(() => controller.abort(), this.timeoutMs);

      const request = buildJsonRpcRequest(method, params);

      try {
        const response = await fetchImpl(url, {
          method: 'POST',
          headers: {
            'Content-Type': 'application/json',
          },
          signal: controller.signal,
          body: JSON.stringify(request),
        });

        if (!response.ok) {
          throw new Error(`HTTP ${response.status}: ${response.statusText}`);
        }

        const json = await response.json();

        if (json.error) {
          const codePart = typeof json.error.code === 'number' ? ` (code ${json.error.code})` : '';
          const message = typeof json.error.message === 'string' ? json.error.message : 'RPC error';
          const responseError = {
            code: typeof json.error.code === 'number' ? json.error.code : undefined,
            message,
            data: json.error.data,
          };

          if (shouldDebugRpcPayloads()) {
            console.error('[wallet-rpc] request failed with RPC error', {
              url,
              method,
              request,
              responseError,
            });
          }

          throw new RpcResponseError(`${message}${codePart}`, responseError);
        }

        // Success - clear failed status
        this.failedUrls.delete(url);
        return json.result;
      } catch (error: unknown) {
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

  async sendRawTransaction(rawTx: string): Promise<string> {
    // Node signature: rpc/methods/tx.py defines tx.sendRawTransaction(rawTx: str),
    // and dispatcher keyword-binding accepts params object form { rawTx: '0x...' }.
    const params = { rawTx };
    validateSendRawTransactionParams(params);
    return this.call('tx.sendRawTransaction', params);
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
