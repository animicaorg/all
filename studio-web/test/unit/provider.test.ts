import { describe, it, expect, beforeEach, vi } from 'vitest';

type AnyRecord = Record<string, any>;
const g: AnyRecord = globalThis as AnyRecord;

function ensureWindow() {
  if (!('window' in g)) g.window = g;
}

async function loadProviderModule() {
  // Reload module fresh each time to observe current global/window state
  vi.resetModules();
  return (await import('../../src/services/provider')) as AnyRecord;
}

describe('services/provider — wallet extension detection', () => {
  beforeEach(() => {
    ensureWindow();
    delete g.window.animica;
  });

  it('errors when provider is absent', async () => {
    const mod = await loadProviderModule();

    let checks = 0;

    if (typeof mod.getProvider === 'function') {
      expect(mod.getProvider()).toBeNull();
      checks++;
    }

    if (typeof mod.ensureProvider === 'function') {
      expect(() => mod.ensureProvider()).toThrow();
      checks++;
    }

    if (typeof mod.requireProvider === 'function') {
      expect(() => mod.requireProvider()).toThrow();
      checks++;
    }

    if (typeof mod.connect === 'function') {
      await expect(mod.connect()).rejects.toThrow();
      checks++;
    }

    // At least one of the exported detection paths should be validated
    expect(checks).toBeGreaterThan(0);
  });

  it('detects and returns the injected provider when present', async () => {
    ensureWindow();

    const mock = {
      request: vi.fn(async (payload: { method: string }) => {
        // Accept a few common account-request method names
        if (
          payload?.method === 'animica_requestAccounts' ||
          payload?.method === 'wallet_requestPermissions' ||
          payload?.method === 'eth_requestAccounts' ||
          payload?.method === 'requestAccounts'
        ) {
          return ['anim1testaccountxxxxxxxxxxxxxxxxxxxxxx'];
        }
        return null;
      }),
      on: vi.fn(),
      off: vi.fn(),
      isAnimica: true,
    };

    g.window.animica = mock;

    const mod = await loadProviderModule();

    let assertions = 0;

    if (typeof mod.getProvider === 'function') {
      expect(mod.getProvider()).toBe(mock);
      assertions++;
    }

    if (typeof mod.ensureProvider === 'function') {
      const p = mod.ensureProvider();
      expect(p).toBe(mock);
      assertions++;
    }

    if (typeof mod.requireProvider === 'function') {
      const p = mod.requireProvider();
      expect(p).toBe(mock);
      assertions++;
    }

    if (typeof mod.connect === 'function') {
      const res = await mod.connect();
      // connect() may return accounts or void; if accounts returned, make sure it's ours
      if (Array.isArray(res)) {
        expect(res[0]).toBe('anim1testaccountxxxxxxxxxxxxxxxxxxxxxx');
      }
      assertions++;
    }

    // Ensure at least one positive detection path executed
    expect(assertions).toBeGreaterThan(0);

    // request() should have been used for connect() if implemented
    if (typeof mod.connect === 'function') {
      expect(mock.request).toHaveBeenCalled();
    }
  });
});
