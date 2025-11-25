import { promises as fs } from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const __dirname = fileURLToPath(new URL('.', import.meta.url));

export const websiteRoot = path.resolve(__dirname, '..', '..');
export const repoRoot = path.resolve(websiteRoot, '..');

export async function walkFiles(dir: string): Promise<string[]> {
  const out: string[] = [];
  const stack = [dir];
  while (stack.length) {
    const current = stack.pop()!;
    const entries = await fs.readdir(current, { withFileTypes: true });
    for (const ent of entries) {
      const full = path.join(current, ent.name);
      if (ent.isDirectory()) {
        stack.push(full);
      } else if (ent.isFile()) {
        out.push(full);
      }
    }
  }
  return out;
}

export function makeRouteRegexFromPage(relPath: string): RegExp | null {
  const noExt = relPath.replace(/\.(astro|md|mdx|ts|tsx)$/i, '');
  const normalized = noExt.replace(/index$/, '');
  const segments = normalized
    .split(path.sep)
    .filter(Boolean)
    .map((seg) => {
      const dyn = seg.match(/^\[(\.\.\.)?(.*)\]$/);
      if (dyn) {
        return dyn[1] ? '(.+)' : '([^/]+)';
      }
      return seg.replace(/\s+/g, '-');
    });
  const route = '/' + segments.join('/');
  return new RegExp(`^${route || '/'}(?:/)?$`);
}

export async function loadRedirects(): Promise<RegExp[]> {
  const redirectsFile = path.join(websiteRoot, '_redirects');
  try {
    const content = await fs.readFile(redirectsFile, 'utf8');
    const rules = content
      .split(/\r?\n/)
      .map((line) => line.trim())
      .filter((line) => line && !line.startsWith('#'))
      .map((line) => line.split(/\s+/)[0]);
    return rules.map((rule) => {
      const escaped = rule
        .replace(/[-/\\^$+?.()|[\]{}]/g, '\\$&')
        .replace(/\\\*/g, '.*')
        .replace(/:splat/g, '.*');
      return new RegExp(`^${escaped}$`);
    });
  } catch {
    return [];
  }
}

export function isRedirected(pathname: string, redirects: RegExp[]): boolean {
  return redirects.some((re) => re.test(pathname));
}

export function slugFromDocPath(file: string, docsRoot: string): string {
  const rel = path.relative(docsRoot, file).split(path.sep).join('/');
  const noExt = rel.replace(/\.(md|mdx)$/i, '');
  return `/docs/${noExt}`.replace(/\/index$/i, '/docs');
}

export function extractSidebarHrefs(sidebarContent: string): string[] {
  const hrefs: string[] = [];
  const re = /^\s*href:\s*(.+)$/gim;
  let m: RegExpExecArray | null;
  while ((m = re.exec(sidebarContent))) {
    const raw = m[1].trim();
    if (raw) hrefs.push(raw);
  }
  return hrefs;
}

export function extractLinksFromMdx(content: string): string[] {
  const links: string[] = [];
  const mdLink = /(^|[^!])\[[^\]]+\]\(([^)]+)\)/gm;
  let m: RegExpExecArray | null;
  while ((m = mdLink.exec(content))) {
    links.push(m[2].trim());
  }

  const hrefDouble = /href="([^"]+)"/gm;
  while ((m = hrefDouble.exec(content))) {
    links.push(m[1].trim());
  }

  const hrefSingle = /href='([^']+)'/gm;
  while ((m = hrefSingle.exec(content))) {
    links.push(m[1].trim());
  }

  return links;
}

export function collectAnchorsFromMdx(content: string): Set<string> {
  const anchors = new Set<string>();
  const headingRe = /^(#{1,6})\s+(.+)$/gm;
  let m: RegExpExecArray | null;
  while ((m = headingRe.exec(content))) {
    anchors.add(slugifyHeading(m[2]));
  }

  const explicit = /<(?:a|span)[^>]*id=["']([^"']+)["'][^>]*>/gim;
  while ((m = explicit.exec(content))) {
    anchors.add(m[1]);
  }
  return anchors;
}

export function slugifyHeading(text: string): string {
  return text
    .toLowerCase()
    .replace(/`([^`]+)`/g, '$1')
    .replace(/[^\w\s-]/g, '')
    .trim()
    .replace(/\s+/g, '-');
}
