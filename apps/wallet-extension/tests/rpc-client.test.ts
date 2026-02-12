import { afterEach, describe, expect, it, vi } from 'vitest';
import { RpcClient } from '../src/core/rpc/client';

describe('RpcClient numeric normalization', () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it('parses chain id returned as hex quantity', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => ({
      ok: true,
      json: async () => ({ result: '0x1' }),
    })) as any);

    const client = new RpcClient(['https://example.invalid/rpc']);
    await expect(client.getChainId()).resolves.toBe(1);
  });

  it('parses nonce returned as decimal string', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => ({
      ok: true,
      json: async () => ({ result: '42' }),
    })) as any);

    const client = new RpcClient(['https://example.invalid/rpc']);
    await expect(client.getNonce('anim1abc')).resolves.toBe(42);
  });
});
