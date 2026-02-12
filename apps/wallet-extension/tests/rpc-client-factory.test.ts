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

import { getRpcClient, clearRpcClient } from '../src/services/rpcClientFactory';
import { setRpcUrl, resetRpcUrl } from '../src/services/rpcConfig';

describe('rpcClientFactory', () => {
  beforeEach(async () => {
    storageState.clear();
    clearRpcClient();
    await resetRpcUrl();
  });

  it('recreates client when RPC setting changes', async () => {
    const initialClient = await getRpcClient();

    await setRpcUrl('http://127.0.0.1:8545/rpc');
    const changedClient = await getRpcClient();

    expect(changedClient).not.toBe(initialClient);
    expect(changedClient.getActiveUrl()).toBe('http://127.0.0.1:8545/rpc');
  });
});
