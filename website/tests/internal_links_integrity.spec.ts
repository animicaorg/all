import { describe, it, expect } from 'vitest';
import path from 'node:path';
import { promises as fs } from 'node:fs';
import {
  collectAnchorsFromMdx,
  extractLinksFromMdx,
  isRedirected,
  loadRedirects,
  makeRouteRegexFromPage,
  slugFromDocPath,
  slugifyHeading,
  walkFiles,
  websiteRoot,
} from './utils/docsTestUtils';

describe('Docs internal link integrity', () => {
  it('verifies MDX internal links resolve to routes or redirects', async () => {
    const docsDir = path.join(websiteRoot, 'src', 'docs');
    const docFiles = (await walkFiles(docsDir)).filter((f) => f.match(/\.mdx?$/i));

    const redirects = await loadRedirects();

    const pageFiles = (await walkFiles(path.join(websiteRoot, 'src', 'pages'))).filter((f) =>
      f.match(/\.(astro|md|mdx|ts|tsx)$/i),
    );
    const pageMatchers = pageFiles
      .map((file) => path.relative(path.join(websiteRoot, 'src', 'pages'), file))
      .map((rel) => makeRouteRegexFromPage(rel))
      .filter((re): re is RegExp => Boolean(re));

    const docRoutes = new Set(docFiles.map((f) => slugFromDocPath(f, docsDir)));
    const anchorMap = new Map<string, Set<string>>();

    for (const file of docFiles) {
      const route = slugFromDocPath(file, docsDir);
      const content = await fs.readFile(file, 'utf8');
      anchorMap.set(route, collectAnchorsFromMdx(content));

      const links = extractLinksFromMdx(content).filter((href) => href.startsWith('/') || href.startsWith('#'));
      for (const raw of links) {
        const link = raw.trim();
        let pathname: string;
        let hash: string | null = null;
        if (link.startsWith('#')) {
          pathname = route;
          hash = link.slice(1);
        } else {
          [pathname, hash] = link.split('#');
        }

        const hasPage = pageMatchers.some((re) => re.test(pathname));
        const hasDoc = docRoutes.has(pathname);
        const redirected = isRedirected(pathname, redirects);

        expect(hasPage || hasDoc || redirected).toBe(true);

        if (hash && hasDoc && !redirected) {
          const anchors = anchorMap.get(pathname) || new Set();
          const normalized = hash.toLowerCase();
          const slugged = slugifyHeading(hash);
          expect(anchors.has(normalized) || anchors.has(slugged)).toBe(true);
        }
      }
    }
  });
});
