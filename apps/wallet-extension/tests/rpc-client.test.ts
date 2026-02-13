import { afterEach, describe, expect, it, vi } from 'vitest';

type FetchImpl = (...args: any[]) => any;

async function createClient(fetchImpl: FetchImpl) {
  vi.resetModules();
  vi.doMock('../src/runtime/env', () => ({
    fetchFn: fetchImpl,
    setTimeoutFn: setTimeout,
    clearTimeoutFn: clearTimeout,
  }));

  const { RpcClient } = await import('../src/core/rpc/client');
  return new RpcClient(['https://example.invalid/rpc']);
}

afterEach(() => {
  vi.resetModules();
  vi.restoreAllMocks();
});

describe('RpcClient numeric normalization', () => {
  it('parses chain id returned as hex quantity', async () => {
    const client = await createClient(vi.fn(async () => ({
      ok: true,
      json: async () => ({ result: '0x1' }),
    })) as any);

    await expect(client.getChainId()).resolves.toBe(1);
  });

  it('parses nonce returned as decimal string', async () => {
    const client = await createClient(vi.fn(async () => ({
      ok: true,
      json: async () => ({ result: '42' }),
    })) as any);

    await expect(client.getNonce('anim1abc')).resolves.toBe(42);
  });
});

describe('RpcClient sendRawTransaction error handling', () => {
  
  it('sends tx.sendRawTransaction params as object { rawTx }', async () => {
    const fetchMock = vi.fn(async (_url: string, init: RequestInit) => ({
      ok: true,
      json: async () => ({ result: '0xhash' }),
    })) as any;

    const client = await createClient(fetchMock);
    await expect(client.sendRawTransaction('0xabcd')).resolves.toBe('0xhash');

    const [, init] = fetchMock.mock.calls[0];
    const payload = JSON.parse(String(init.body));
    expect(payload.method).toBe('tx.sendRawTransaction');
    expect(payload.params).toEqual({ rawTx: '0xabcd' });
  });

  it('throws local validation error for invalid rawTx shape before network call', async () => {
    const fetchMock = vi.fn(async () => ({ ok: true, json: async () => ({ result: 'x' }) })) as any;
    const client = await createClient(fetchMock);

    await expect(client.sendRawTransaction('abc')).rejects.toThrow('expected 0x-prefixed hex string');
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it('surfaces RPC response errors without masking them as endpoint failures', async () => {
    const client = await createClient(vi.fn(async () => ({
      ok: true,
      json: async () => ({
        error: {
          code: -32011,
          message: 'Invalid signature',
        },
      }),
    })) as any);

    await expect(client.sendRawTransaction('0xabcd')).rejects.toThrow('Invalid signature (code -32011)');
  });

  it('includes useful details for thrown non-Error values', async () => {
    const client = await createClient(vi.fn(async () => {
      throw { reason: 'socket hang up' };
    }) as any);

    await expect(client.sendRawTransaction('0xabcd')).rejects.toThrow(
      'All RPC endpoints failed. Last error: Unknown error: [object Object]',
    );
  });
});
