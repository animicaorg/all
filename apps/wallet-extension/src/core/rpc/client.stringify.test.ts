import { describe, expect, it, vi } from 'vitest';

describe('RpcClient safe request serialization', () => {
  it('serializes bigint params without JSON stringify crashes', async () => {
    vi.resetModules();
    const fetchMock = vi.fn(async (_url: string, init?: RequestInit) => {
      return {
        ok: true,
        status: 200,
        text: async () => '{"jsonrpc":"2.0","id":1,"result":"0x1"}',
      } as any;
    });

    (globalThis as any).fetch = fetchMock;

    const { RpcClient } = await import('./client');
    const client = new RpcClient(['https://rpc.example/rpc']);

    await client.call('debug.echo', [{ value: 99n } as any]);

    const body = String(fetchMock.mock.calls[0][1]?.body);
    expect(body).toContain('"99"');
    expect(body).not.toContain('99n');
  });
});
