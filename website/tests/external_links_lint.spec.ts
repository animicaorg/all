import { describe, it, expect } from 'vitest';
import path from 'node:path';
import { promises as fs } from 'node:fs';
import { extractLinksFromMdx, walkFiles, websiteRoot } from './utils/docsTestUtils';

function isExternal(href: string): boolean {
  return /^https?:\/\//i.test(href);
}

describe('External link lint', () => {
  it('accepts only well-formed http(s) links', async () => {
    const docsDir = path.join(websiteRoot, 'src', 'docs');
    const docFiles = (await walkFiles(docsDir)).filter((f) => f.match(/\.mdx?$/i));

    const invalid: string[] = [];

    for (const file of docFiles) {
      const content = await fs.readFile(file, 'utf8');
      const links = extractLinksFromMdx(content).filter(isExternal);
      for (const link of links) {
        try {
          const url = new URL(link);
          if (!(url.protocol === 'http:' || url.protocol === 'https:')) {
            invalid.push(link);
          }
        } catch {
          invalid.push(link);
        }
      }
    }

    expect(invalid).toEqual([]);
  });
});
