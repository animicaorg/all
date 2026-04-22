import { describe, expect, it } from 'vitest';
import { addUsdanToWallet, getAnimicaProvider, getChainId } from '../lib/wallet';

describe('wallet connect utilities', () => {
  it('returns null provider when extension is absent', () => {
    (globalThis as any).window = {};
    expect(getAnimicaProvider()).toBeNull();
  });

  it('normalizes hex chain ids from provider', async () => {
    (globalThis as any).window = {
      animica: {
        request: async ({ method }: { method: string }) => {
          if (method === 'animica_chainId') return '0x539';
          return null;
        }
      }
    };

    await expect(getChainId()).resolves.toBe(1337);
  });

  it('tries both wallet asset registration methods', async () => {
    let called = 0;
    (globalThis as any).window = {
      animica: {
        request: async ({ method }: { method: string }) => {
          called += 1;
          if (method === 'animica_watchAsset') throw new Error('unsupported');
          return true;
        }
      }
    };

    const ok = await addUsdanToWallet({ tokenAddress: 'anim1token' });
    expect(ok).toBe(true);
    expect(called).toBe(2);
  });
});
