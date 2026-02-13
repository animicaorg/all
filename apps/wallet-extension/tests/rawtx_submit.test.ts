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
  it('probes next mode when first mode returns -32602', async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(resp(200, { jsonrpc: '2.0', id: 1, error: { code: -32602, message: 'Invalid params' } }))
      .mockResolvedValueOnce(resp(200, { jsonrpc: '2.0', id: 1, result: { txid: '0xabc' } }));

    const { submitRawTransactionCompat } = await importSubmitter(fetchMock);
    const out = await submitRawTransactionCompat({
      rpcUrl: 'https://mainnet.animica.org/rpc',
      chainId: 1,
      rawTx: '0xdeadbeef',
      timeoutMs: 5000,
      forceCompat: true,
    });

    expect(out.ok).toBe(true);
    expect(out.modeUsed).toBe('array:obj:rawTx:hex');

    const first = JSON.parse(String(fetchMock.mock.calls[0][1].body));
    const second = JSON.parse(String(fetchMock.mock.calls[1][1].body));
    expect(first.params).toEqual(['0xdeadbeef']);
    expect(second.params).toEqual([{ rawTx: '0xdeadbeef' }]);
  });

  it('invalidates cached mode on -32602 and caches new mode', async () => {
    const seeded = {
      rawtx_compat_mode_cache_v1: {
        '1::https://mainnet.animica.org/rpc': { mode: 'obj:rawTx:hex', ts: Date.now() },
      },
    };

    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(resp(200, { jsonrpc: '2.0', id: 1, error: { code: -32602, message: 'Invalid params' } }))
      .mockResolvedValueOnce(resp(200, { jsonrpc: '2.0', id: 1, result: '0xhash' }));

    const { submitRawTransactionCompat, store } = await importSubmitter(fetchMock, seeded);
    const out = await submitRawTransactionCompat({
      rpcUrl: 'https://mainnet.animica.org/rpc',
      chainId: 1,
      rawTx: '0xdeadbeef',
      timeoutMs: 5000,
      forceCompat: true,
    });

    expect(out.ok).toBe(true);
    const cache = (store.rawtx_compat_mode_cache_v1 as any);
    expect(cache['1::https://mainnet.animica.org/rpc'].mode).toBe('array:string:hex');
  });

  it('falls back to base64 after all hex variants fail with invalid params', async () => {
    const fetchMock = vi.fn(async (_url: string, init: RequestInit) => {
      const body = JSON.parse(String(init.body));
      const params = JSON.stringify(body.params);
      if (params.includes('0x')) {
        return resp(200, { jsonrpc: '2.0', id: 1, error: { code: -32602, message: 'Invalid params' } });
      }
      return resp(200, { jsonrpc: '2.0', id: 1, result: { hash: '0xb64ok' } });
    });

    const { submitRawTransactionCompat } = await importSubmitter(fetchMock);
    const out = await submitRawTransactionCompat({
      rpcUrl: 'https://mainnet.animica.org/rpc',
      chainId: 1,
      rawTx: '0xdeadbeef',
      timeoutMs: 5000,
      forceCompat: true,
    });

    expect(out.ok).toBe(true);
    expect(out.modeUsed).toBe('array:obj:rawTxB64:b64');
  });

  it('treats transport ambiguity as success when post-check finds tx', async () => {
    const fetchMock = vi.fn(async (_url: string, init: RequestInit) => {
      const body = JSON.parse(String(init.body));
      if (body.method === 'tx.sendRawTransaction') {
        throw new Error('gateway timeout');
      }
      if (body.method === 'tx.getTransactionByHash') {
        return resp(200, { jsonrpc: '2.0', id: 1, result: { hash: '0xfound' } });
      }
      return resp(200, { jsonrpc: '2.0', id: 1, result: null });
    });

    const { submitRawTransactionCompat } = await importSubmitter(fetchMock);
    const out = await submitRawTransactionCompat({
      rpcUrl: 'https://mainnet.animica.org/rpc',
      chainId: 1,
      rawTx: '0xdeadbeef',
      timeoutMs: 5000,
      forceCompat: true,
      maxRetriesPerMode: 1,
    });

    expect(out.ok).toBe(true);
    expect(out.txid).toMatch(/^0x/);
  });
});

function resp(status: number, body: unknown): Response {
  return {
    ok: status >= 200 && status < 300,
    status,
    text: async () => JSON.stringify(body),
  } as unknown as Response;
}
