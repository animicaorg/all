import { beforeEach, describe, expect, it, vi } from 'vitest';

const storageState = new Map<string, any>();

function createStorage() {
  return {
    async get(keys?: string | string[]) {
      if (!keys) {
        return Object.fromEntries(storageState.entries());
      }

      if (Array.isArray(keys)) {
        return keys.reduce<Record<string, any>>((acc, key) => {
          if (storageState.has(key)) acc[key] = storageState.get(key);
          return acc;
        }, {});
      }

      return storageState.has(keys) ? { [keys]: storageState.get(keys) } : {};
    },
    async set(values: Record<string, any>) {
      Object.entries(values).forEach(([key, value]) => storageState.set(key, value));
    },
    async remove(keys: string | string[]) {
      const normalized = Array.isArray(keys) ? keys : [keys];
      normalized.forEach((key) => storageState.delete(key));
    },
  };
}

vi.stubGlobal('chrome', {
  storage: {
    local: createStorage(),
  },
});

import {
  DEFAULT_RPC,
  getEffectiveRpcUrl,
  getRpcUrl,
  resetRpcUrl,
  setRpcUrl,
  validateRpcUrl,
} from '../src/services/rpcConfig';

describe('rpcConfig', () => {
  beforeEach(async () => {
    storageState.clear();
    await resetRpcUrl();
  });

  it('returns default RPC when no override is present', async () => {
    const url = await getRpcUrl();
    expect(url).toBe(DEFAULT_RPC);
    expect(getEffectiveRpcUrl()).toBe(DEFAULT_RPC);
  });

  it('falls back to provided network RPC when no override exists', async () => {
    const fallback = 'http://127.0.0.1:18546';

    expect(await getRpcUrl(fallback)).toBe('http://127.0.0.1:18546/rpc');
    expect(getEffectiveRpcUrl(fallback)).toBe('http://127.0.0.1:18546/rpc');
  });

  it('set/get/reset RPC override works', async () => {
    await setRpcUrl('http://127.0.0.1:8545/rpc');

    expect(await getRpcUrl()).toBe('http://127.0.0.1:8545/rpc');
    expect(getEffectiveRpcUrl()).toBe('http://127.0.0.1:8545/rpc');

    await resetRpcUrl();

    expect(await getRpcUrl()).toBe(DEFAULT_RPC);
  });

  it('rejects invalid URLs', () => {
    expect(() => validateRpcUrl('javascript:alert(1)')).toThrow('http:// or https://');
    expect(() => validateRpcUrl('ftp://example.com/rpc')).toThrow('http:// or https://');
    expect(() => validateRpcUrl('not-a-url')).toThrow();
  });
});
