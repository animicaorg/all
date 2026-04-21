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

  it('resolves pending nonce using fallback methods when needed', async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce({ ok: true, json: async () => ({ error: { code: -32601, message: 'Method not found' } }) })
      .mockResolvedValueOnce({ ok: true, json: async () => ({ error: { code: -32601, message: 'Method not found' } }) })
      .mockResolvedValueOnce({ ok: true, json: async () => ({ result: '44' }) });

    const client = await createClient(fetchMock as any);
    await expect(client.getPendingNonce('anim1abc')).resolves.toBe(44);

    const first = JSON.parse(String(fetchMock.mock.calls[0][1].body));
    const second = JSON.parse(String(fetchMock.mock.calls[1][1].body));
    const third = JSON.parse(String(fetchMock.mock.calls[2][1].body));

    expect(first.method).toBe('state.getPendingNonce');
    expect(second.method).toBe('state.getNextNonce');
    expect(third.method).toBe('state.getNonce');
    expect(third.params).toEqual(['anim1abc', 'pending']);
  });

  it('does not hide non-method RPC errors while resolving pending nonce', async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce({ ok: true, json: async () => ({ error: { code: -32000, message: 'nonce lookup failed' } }) });

    const client = await createClient(fetchMock as any);
    await expect(client.getPendingNonce('anim1abc')).rejects.toThrow('nonce lookup failed');
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });
});

describe('RpcClient sendRawTransaction error handling', () => {
  
  it('sends tx.sendRawTransaction in compatibility mode order (array string first)', async () => {
    const fetchMock = vi.fn(async (_url: string, init: RequestInit) => ({
      ok: true,
      json: async () => ({ result: '0xhash' }),
    })) as any;

    const client = await createClient(fetchMock);
    await expect(client.sendRawTransaction('0xabcd')).resolves.toBe('0xhash');

    const [, init] = fetchMock.mock.calls[0];
    const payload = JSON.parse(String(init.body));
    expect(payload.method).toBe('tx.sendRawTransaction');
    expect(payload.params).toEqual(['0xabcd']);
  });

  it('throws local validation error for invalid rawTx shape before network call', async () => {
    const fetchMock = vi.fn(async () => ({ ok: true, json: async () => ({ result: 'x' }) })) as any;
    const client = await createClient(fetchMock);

    await expect(client.sendRawTransaction('abc')).rejects.toThrow('hex length must be even');
    await expect(client.sendRawTransaction('0xzz')).rejects.toThrow('expected hex string');
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it('retries with the next compatibility shape on -32602', async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce({ ok: true, json: async () => ({ error: { code: -32602, message: 'Invalid params' } }) })
      .mockResolvedValueOnce({ ok: true, json: async () => ({ result: '0xhash' }) });

    const client = await createClient(fetchMock as any);
    await expect(client.sendRawTransaction('abcd')).resolves.toBe('0xhash');

    const firstPayload = JSON.parse(String(fetchMock.mock.calls[0][1].body));
    const secondPayload = JSON.parse(String(fetchMock.mock.calls[1][1].body));
    expect(firstPayload.params).toEqual(['0xabcd']);
    expect(secondPayload.params).toEqual([{ rawTx: '0xabcd' }]);
    expect(secondPayload.id).toBe(firstPayload.id);
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

    await expect(client.sendRawTransaction('0xabcd')).rejects.toThrow('Failed to submit raw transaction in all compatibility modes');
  });
});
