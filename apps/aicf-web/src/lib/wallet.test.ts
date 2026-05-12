import { describe, expect, it } from 'vitest';
import { connectWallet, getAnimicaProvider } from './wallet';

describe('wallet helper', () => {
  it('returns null when no provider exists', () => {
    (window as any).animica = undefined;
    expect(getAnimicaProvider()).toBeNull();
  });

  it('returns provider when present', () => {
    (window as any).animica = { request: async () => [] };
    expect(getAnimicaProvider()).not.toBeNull();
  });

  it('throws when wallet provider is absent', async () => {
    (window as any).animica = undefined;
    await expect(connectWallet()).rejects.toThrow(/provider not detected/i);
  });

  it('uses animica_requestAccounts when provider exists', async () => {
    (window as any).animica = {
      request: async ({ method }: { method: string }) => {
        if (method === 'animica_requestAccounts') {
          return ['anim1qqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqq'];
        }
        throw new Error('unsupported');
      },
    };

    await expect(connectWallet()).resolves.toEqual(['anim1qqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqq']);
  });
});
