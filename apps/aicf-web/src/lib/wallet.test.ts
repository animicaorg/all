import { describe, expect, it } from 'vitest';
import { getAnimicaProvider } from './wallet';

describe('wallet helper', () => {
  it('returns null when no provider exists', () => {
    (window as any).animica = undefined;
    expect(getAnimicaProvider()).toBeNull();
  });

  it('returns provider when present', () => {
    (window as any).animica = { request: async () => [] };
    expect(getAnimicaProvider()).not.toBeNull();
  });
});
