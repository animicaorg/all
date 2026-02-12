// RPC client for Animica nodes

import { clearTimeoutFn, fetchFn, setTimeoutFn } from '../../runtime/env';

const RPC_TIMEOUT_MS = 10000;

class RpcResponseError extends Error {
  constructor(message: string) {
    super(message);
    this.name = 'RpcResponseError';
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

  async call(method: string, params: any[] = []): Promise<any> {
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

      try {
        const response = await fetchImpl(url, {
          method: 'POST',
          headers: {
            'Content-Type': 'application/json',
          },
          signal: controller.signal,
          body: JSON.stringify({
            jsonrpc: '2.0',
            id: Date.now(),
            method,
            params,
          }),
        });

        if (!response.ok) {
          throw new Error(`HTTP ${response.status}: ${response.statusText}`);
        }

        const json = await response.json();

        if (json.error) {
          const codePart = typeof json.error.code === 'number' ? ` (code ${json.error.code})` : '';
          const message = typeof json.error.message === 'string' ? json.error.message : 'RPC error';
          throw new RpcResponseError(`${message}${codePart}`);
        }

        // Success - clear failed status
        this.failedUrls.delete(url);
        return json.result;
      } catch (error: unknown) {
        if (error instanceof RpcResponseError) {
          throw error;
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
    return this.call('tx.sendRawTransaction', [rawTx]);
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
