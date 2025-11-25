import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';

/**
 * Unit tests for src/config/links.ts
 *
 * We verify that computed links honor PUBLIC_* env vars and fall back to
 * sane in-site routes when env is not provided.
 *
 * The test is defensive to slight API differences: it tries to extract
 * base URLs from several common export shapes.
 */

type LinksModule = Record<string, any>;

const ORIGINAL_ENV = { ...process.env };

function setEnv(env: Record<string, string | undefined>) {
  for (const k of Object.keys(env)) {
    if (typeof env[k] === 'undefined') {
      delete (process.env as any)[k];
    } else {
      (process.env as any)[k] = env[k]!;
    }
  }
}

function resetEnv() {
  process.env = { ...ORIGINAL_ENV };
}

/** Dynamically import the module with a fresh cache */
async function loadLinksModule(): Promise<LinksModule> {
  vi.resetModules();
  // Import path relative to project root: website/src/config/links.ts
  return await import('../../src/config/links');
}

/** Try to read a base URL for a given key ('studio'|'explorer'|'docs') */
function readBase(mod: LinksModule, key: 'studio' | 'explorer' | 'docs'): string | undefined {
  // 1) links.{key}
  if (mod.links && typeof mod.links[key] === 'string') return mod.links[key];

  // 2) UPPER_URL ex: STUDIO_URL / EXPLORER_URL / DOCS_URL
  const upperKey = `${key.toUpperCase()}_URL`;
  if (typeof mod[upperKey] === 'string') return mod[upperKey];

  // 3) direct export: studio / explorer / docs as strings
  if (typeof mod[key] === 'string') return mod[key];

  // 4) base map: bases.{key}
  if (mod.bases && typeof mod.bases[key] === 'string') return mod.bases[key];

  return undefined;
}

describe('config/links → env to computed links', () => {
  beforeEach(() => {
    vi.resetModules();
  });

  afterEach(() => {
    resetEnv();
  });

  it('honors PUBLIC_* env overrides', async () => {
    setEnv({
      PUBLIC_STUDIO_URL: 'https://studio.example.dev',
      PUBLIC_EXPLORER_URL: 'https://explorer.example.dev',
      PUBLIC_DOCS_URL: 'https://docs.example.dev',
    });

    const mod = await loadLinksModule();

    const studio = readBase(mod, 'studio');
    const explorer = readBase(mod, 'explorer');
    const docs = readBase(mod, 'docs');

    expect(studio, 'studio base url').toBe('https://studio.example.dev');
    expect(explorer, 'explorer base url').toBe('https://explorer.example.dev');
    expect(docs, 'docs base url').toBe('https://docs.example.dev');
  });

  it('falls back to internal routes when env is unset', async () => {
    setEnv({
      PUBLIC_STUDIO_URL: undefined,
      PUBLIC_EXPLORER_URL: undefined,
      PUBLIC_DOCS_URL: undefined,
    });

    const mod = await loadLinksModule();

    const studio = readBase(mod, 'studio');
    const explorer = readBase(mod, 'explorer');
    const docs = readBase(mod, 'docs');

    expect(studio, 'studio base defined').toBeTruthy();
    expect(explorer, 'explorer base defined').toBeTruthy();
    expect(docs, 'docs base defined').toBeTruthy();

    // Accept either internal routes (/studio, /explorer, /docs)
    // or absolute defaults that contain those path segments.
    const expectPathish = (val: string | undefined, seg: string) => {
      expect(val).toBeTypeOf('string');
      const v = String(val);
      expect(
        v.startsWith(`/${seg}`) || v.includes(`/${seg}`)
      ).toBeTruthy();
    };

    expectPathish(studio, 'studio');
    expectPathish(explorer, 'explorer');
    expectPathish(docs, 'docs');
  });
});
