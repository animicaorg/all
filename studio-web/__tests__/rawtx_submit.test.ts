import { beforeEach, describe, expect, it, vi } from 'vitest';
import { submitRawTxCompat } from '../src/rpc/rawtx_submit';

const rawHex = '0xdeadbeef';

describe('submitRawTxCompat', () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    localStorage.clear();
  });

  it('falls through mode ordering on invalid params and succeeds', async () => {
    const fetchMock = vi.spyOn(globalThis, 'fetch' as any).mockImplementation(async (_url: any, init: any) => {
      const body = JSON.parse(init.body as string);
      const params = JSON.stringify(body.params);
      if (params === JSON.stringify([rawHex])) {
        return mkResp(200, { jsonrpc: '2.0', id: 1, error: { code: -32602, message: 'Invalid params' } });
      }
      if (params === JSON.stringify([{ rawTx: rawHex }])) {
        return mkResp(200, { jsonrpc: '2.0', id: 1, result: { txid: '0xabc123' } });
      }
      return mkResp(200, { jsonrpc: '2.0', id: 1, error: { code: -32602, message: 'Invalid params' } });
    });

    const out = await submitRawTxCompat({ rpcUrl: 'https://mainnet.animica.org/rpc', chainId: 1, rawTx: rawHex, forceCompat: true });

    expect(out.ok).toBe(true);
    expect(out.txid).toBe('0xabc123');
    expect(out.modeUsed).toBe('array:obj:rawTx:hex');
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });

  it('uses cached mode first and invalidates on -32602', async () => {
    localStorage.setItem(
      'studio.rawtx.compat.mode.v1',
      JSON.stringify({ '1::https://mainnet.animica.org/rpc': { mode: 'obj:rawTx:hex', ts: Date.now() } }),
    );

    vi.spyOn(globalThis, 'fetch' as any).mockImplementation(async (_url: any, init: any) => {
      const body = JSON.parse(init.body as string);
      if (body.params?.rawTx) {
        return mkResp(200, { jsonrpc: '2.0', id: 1, error: { code: -32602, message: 'Invalid params' } });
      }
      return mkResp(200, { jsonrpc: '2.0', id: 1, result: '0xhash' });
    });

    const out = await submitRawTxCompat({ rpcUrl: 'https://mainnet.animica.org/rpc', chainId: 1, rawTx: rawHex, forceCompat: true });
    expect(out.ok).toBe(true);
    expect(out.modeUsed).toBe('array:string:hex');

    const cache = JSON.parse(localStorage.getItem('studio.rawtx.compat.mode.v1') || '{}');
    expect(cache['1::https://mainnet.animica.org/rpc'].mode).toBe('array:string:hex');
  });

  it('resolves ambiguous transport failure via post-check', async () => {
    let call = 0;
    vi.spyOn(globalThis, 'fetch' as any).mockImplementation(async (_url: any, init: any) => {
      call += 1;
      const body = JSON.parse(init.body as string);
      if (body.method === 'tx.sendRawTransaction') {
        throw new Error('gateway timeout');
      }
      if (body.method === 'tx.getTransactionByHash') {
        return mkResp(200, { jsonrpc: '2.0', id: 1, result: { hash: '0xresolved' } });
      }
      return mkResp(200, { jsonrpc: '2.0', id: 1, result: null });
    });

    const out = await submitRawTxCompat({
      rpcUrl: 'https://mainnet.animica.org/rpc',
      chainId: 1,
      rawTx: rawHex,
      forceCompat: true,
      maxRetriesPerMode: 1,
    });

    expect(out.ok).toBe(true);
    expect(out.txid).toMatch(/^0x/);
    expect(call).toBeGreaterThan(1);
  });
});

describe('rawtx compatibility integration scaffolding (mocked RPC matrix)', () => {
  it('exercises base64 fallback after all hex shapes reject with invalid params', async () => {
    vi.spyOn(globalThis, 'fetch' as any).mockImplementation(async (_url: any, init: any) => {
      const body = JSON.parse(init.body as string);
      const p = body.params;
      const isHexMode = JSON.stringify(p).includes('0x');
      if (isHexMode) {
        return mkResp(200, { jsonrpc: '2.0', id: 1, error: { code: -32602, message: 'Invalid params' } });
      }
      return mkResp(200, { jsonrpc: '2.0', id: 1, result: { hash: '0xb64' } });
    });

    const out = await submitRawTxCompat({ rpcUrl: 'https://mainnet.animica.org/rpc', chainId: 1, rawTx: rawHex, forceCompat: true });
    expect(out.ok).toBe(true);
    expect(out.modeUsed).toBe('array:obj:rawTxB64:b64');
  });
});

function mkResp(status: number, body: unknown): Response {
  return {
    ok: status >= 200 && status < 300,
    status,
    text: async () => JSON.stringify(body),
  } as unknown as Response;
}
