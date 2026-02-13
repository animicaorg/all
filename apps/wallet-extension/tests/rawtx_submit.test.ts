import { afterEach, describe, expect, it, vi } from 'vitest';

type FetchImpl = (...args: any[]) => any;

async function importSubmitter(fetchImpl: FetchImpl, storageSeed: Record<string, unknown> = {}) {
  vi.resetModules();
  const store = { ...storageSeed } as Record<string, unknown>;

  vi.stubGlobal('chrome', {
    storage: {
      local: {
        get: vi.fn(async (keys: string[]) => {
          const out: Record<string, unknown> = {};
          for (const key of keys) out[key] = store[key];
          return out;
        }),
        set: vi.fn(async (obj: Record<string, unknown>) => {
          Object.assign(store, obj);
        }),
      },
    },
  } as any);

  vi.doMock('../src/runtime/env', () => ({
    fetchFn: fetchImpl,
    setTimeoutFn: setTimeout,
    clearTimeoutFn: clearTimeout,
  }));

  const mod = await import('../src/core/rpc/rawtx_submit');
  return { ...mod, store };
}

afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
  vi.resetModules();
});

describe('submitRawTransactionCompat', () => {
  it('retries on -32602 and succeeds on underscore alias', async () => {
    const fetchMock = vi.fn(async (_url: string, init: RequestInit) => {
      const body = JSON.parse(String(init.body));
      if (body.method === 'rpc.discover') {
        return resp(200, { jsonrpc: '2.0', id: 1, result: { methods: [] } });
      }
      if (body.method === 'tx.sendRawTransaction') {
        return resp(200, { jsonrpc: '2.0', id: 1, error: { code: -32602, message: 'Invalid params' } });
      }
      if (body.method === 'tx_sendRawTransaction') {
        return resp(200, { jsonrpc: '2.0', id: 1, result: '0xabc' });
      }
      return resp(200, { jsonrpc: '2.0', id: 1, error: { code: -32601, message: 'Method not found' } });
    });

    const { submitRawTransactionCompat } = await importSubmitter(fetchMock);
    const out = await submitRawTransactionCompat({
      rpcUrl: 'https://mainnet.animica.org/rpc',
      chainId: 1,
      rawTx: '0xdeadbeef',
      timeoutMs: 5000,
    });

    expect(out.ok).toBe(true);
    expect(out.txid).toBe('0xabc');

    const sendDot = JSON.parse(String(fetchMock.mock.calls[1][1].body));
    const sendUnderscore = JSON.parse(String(fetchMock.mock.calls[2][1].body));
    expect(sendDot.params).toEqual(['0xdeadbeef']);
    expect(sendUnderscore.params).toEqual(['0xdeadbeef']);
  });

  it('does not retry terminal animica tx errors', async () => {
    const fetchMock = vi.fn(async (_url: string, init: RequestInit) => {
      const body = JSON.parse(String(init.body));
      if (body.method === 'rpc.discover') {
        return resp(200, { jsonrpc: '2.0', id: 1, result: { methods: [] } });
      }
      return resp(200, {
        jsonrpc: '2.0',
        id: 1,
        error: { code: -32011, message: 'ChainIdMismatch', data: { expectedChainId: 1, detectedChainId: 9 } },
      });
    });

    const { submitRawTransactionCompat } = await importSubmitter(fetchMock);
    const out = await submitRawTransactionCompat({
      rpcUrl: 'https://mainnet.animica.org/rpc',
      chainId: 1,
      rawTx: '0xdeadbeef',
      timeoutMs: 5000,
    });

    expect(out.ok).toBe(false);
    expect(out.error?.code).toBe(-32011);
    expect(out.error?.message).toContain('Expected 1, detected 9');
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });

  it('rejects malformed rawTx before network call', async () => {
    const fetchMock = vi.fn();
    const { submitRawTransactionCompat } = await importSubmitter(fetchMock);

    await expect(submitRawTransactionCompat({
      rpcUrl: 'https://mainnet.animica.org/rpc',
      chainId: 1,
      rawTx: 'deadbeef',
      timeoutMs: 5000,
    })).rejects.toThrow(/must start with 0x/);

    expect(fetchMock).not.toHaveBeenCalled();
  });
});

function resp(status: number, body: unknown): Response {
  return {
    ok: status >= 200 && status < 300,
    status,
    text: async () => JSON.stringify(body),
  } as unknown as Response;
}
