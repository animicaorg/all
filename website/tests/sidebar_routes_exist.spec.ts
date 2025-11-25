import { describe, it, expect } from 'vitest';
import path from 'node:path';
import { promises as fs } from 'node:fs';
import {
  extractSidebarHrefs,
  isRedirected,
  loadRedirects,
  makeRouteRegexFromPage,
  slugFromDocPath,
  walkFiles,
  websiteRoot,
} from './utils/docsTestUtils';

describe('SIDEBAR routes resolve', () => {
  it('ensures every sidebar href maps to a page, doc, or redirect', async () => {
    const sidebarPath = path.join(websiteRoot, 'src', 'docs', 'SIDEBAR.yaml');
    const sidebarContent = await fs.readFile(sidebarPath, 'utf8');
    const hrefs = extractSidebarHrefs(sidebarContent).filter((h) => h.startsWith('/'));

    const redirects = await loadRedirects();

    const pageFiles = (await walkFiles(path.join(websiteRoot, 'src', 'pages'))).filter((f) =>
      f.match(/\.(astro|md|mdx|ts|tsx)$/i),
    );
    const pageMatchers = pageFiles
      .map((file) => path.relative(path.join(websiteRoot, 'src', 'pages'), file))
      .map((rel) => makeRouteRegexFromPage(rel))
      .filter((re): re is RegExp => Boolean(re));

    const docsDir = path.join(websiteRoot, 'src', 'docs');
    const docRoutes = new Set(
      (await walkFiles(docsDir))
        .filter((f) => f.match(/\.mdx?$/i))
        .map((f) => slugFromDocPath(f, docsDir)),
    );

    const missing: string[] = [];
    for (const href of hrefs) {
      const [pathname] = href.split('#');
      const hasPage = pageMatchers.some((re) => re.test(pathname));
      const hasDoc = docRoutes.has(pathname);
      const redirected = isRedirected(pathname, redirects);
      if (!hasPage && !hasDoc && !redirected) {
        missing.push(href);
      }
    }

    expect(missing).toEqual([]);
  });
});
