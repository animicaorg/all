import { afterEach, describe, expect, it, vi } from 'vitest';

describe('rpcPing runtime compatibility', () => {
  afterEach(() => {
    vi.unstubAllGlobals();
    vi.resetModules();
  });

  it('works in node test runtime without window', async () => {
    vi.stubGlobal('window', undefined);
    vi.stubGlobal('fetch', vi.fn(async () => ({
      ok: true,
      json: async () => ({ result: { node_id: 'node-a', chain_id: '0x1' } }),
    })) as any);

    const { rpcPing } = await import('../src/services/rpcPing');
    const result = await rpcPing({ rpcUrl: 'https://example.invalid/rpc', timeoutMs: 1000 });
    expect(result.ok).toBe(true);
    expect(result.chainId).toBe(1);
    expect(result.nodeId).toBe('node-a');
  });

  it('falls back to chain.getHead when system.ping errors', async () => {
    const fetchMock = vi.fn()
      .mockResolvedValueOnce({ ok: true, json: async () => ({ error: { code: -32601, message: 'method not found' } }) })
      .mockResolvedValueOnce({ ok: true, json: async () => ({ result: { chainId: '1' } }) });

    vi.stubGlobal('fetch', fetchMock as any);

    const { rpcPing } = await import('../src/services/rpcPing');
    const result = await rpcPing({ rpcUrl: 'https://example.invalid/rpc', timeoutMs: 1000 });
    expect(result.ok).toBe(true);
    expect(result.chainId).toBe(1);
  });
});
