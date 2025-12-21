import { describe, it, expect } from 'vitest';
import path from 'node:path';
import { existsSync } from 'node:fs';

import { ENV } from '../../src/env';

const websiteRoot = path.resolve(__dirname, '..', '..');
const pagesRoot = path.join(websiteRoot, 'src', 'pages');

const requiredRoutes = [
  'index.astro',
  'docs.astro',
  'explorer.astro',
  'wallet.astro',
  'node.astro',
  'mine.astro',
  'community.astro',
  'status.astro',
  'updates/index.astro',
];

describe('site routes & env', () => {
  it('has required routes in src/pages', () => {
    const missing = requiredRoutes.filter((route) => !existsSync(path.join(pagesRoot, route)));
    expect(missing).toEqual([]);
  });

  it('exposes required env URLs', () => {
    expect(ENV.RPC_URL).toMatch(/^https?:\/\//);
    expect(ENV.EXPLORER_URL).toMatch(/^https?:\/\//);
    expect(ENV.DOCS_URL).toMatch(/^https?:\/\//);
    expect(ENV.GITHUB_URL).toMatch(/^https?:\/\//);
  });
});
