import { describe, expect, it } from 'vitest';
import { existsSync, readFileSync } from 'node:fs';
import path from 'node:path';

type ProviderManifest = {
  items: Array<{ filename: string; url: string; sha256: string }>;
};

const websiteRoot = path.resolve(__dirname, '..', '..');
const providerDir = path.join(websiteRoot, 'public', 'provider');
const manifestPath = path.join(providerDir, 'manifest.json');

describe('provider artifact references', () => {
  it('manifest exists and references files that are present in public/provider', () => {
    expect(existsSync(manifestPath)).toBe(true);

    const manifest = JSON.parse(readFileSync(manifestPath, 'utf-8')) as ProviderManifest;
    expect(manifest.items.length).toBeGreaterThan(0);

    for (const item of manifest.items) {
      expect(item.sha256).toMatch(/^[a-f0-9]{64}$/);
      expect(item.url.startsWith('/provider/')).toBe(true);

      const expectedPath = path.join(providerDir, item.filename);
      expect(existsSync(expectedPath)).toBe(true);
    }
  });
});
