import { describe, it, expect } from 'vitest';
import { tmpdir } from 'node:os';
import path from 'node:path';
import { mkdtemp, writeFile, mkdir, rm, readFile } from 'node:fs/promises';
import { execFile } from 'node:child_process';
import { promisify } from 'node:util';
import { walkFiles, websiteRoot } from './utils/docsTestUtils';

const execFileAsync = promisify(execFile);

const scriptPath = path.join(websiteRoot, 'scripts', 'sync_docs_from_repo.mjs');

function normalizeList(files: string[], root: string): string[] {
  return files.map((f) => path.relative(root, f).split(path.sep).join('/')).sort();
}

describe('sync_docs_from_repo.mjs (fixture repo)', () => {
  it('copies only allowed docs and preserves SIDEBAR.yaml content', async () => {
    const srcRoot = await mkdtemp(path.join(tmpdir(), 'docs-src-'));
    const destRoot = await mkdtemp(path.join(tmpdir(), 'docs-dest-'));

    try {
      await mkdir(path.join(srcRoot, 'nested'), { recursive: true });
      await mkdir(path.join(srcRoot, 'node_modules', 'ignored'), { recursive: true });

      const sidebar = 'title: Test\nsections:\n  - title: One\n    href: /docs/ONE\n';
      await writeFile(path.join(srcRoot, 'SIDEBAR.yaml'), sidebar, 'utf8');
      await writeFile(path.join(srcRoot, 'ROOT.mdx'), '# Root doc', 'utf8');
      await writeFile(path.join(srcRoot, 'nested', 'ALLOWED.md'), '# Nested doc', 'utf8');
      await writeFile(path.join(srcRoot, 'image.png'), 'PNG', 'utf8');
      await writeFile(path.join(srcRoot, 'IGNORED.txt'), 'skip me', 'utf8');
      await writeFile(path.join(srcRoot, 'node_modules', 'ignored', 'BLOCKED.mdx'), '# should be excluded', 'utf8');

      const { stdout } = await execFileAsync('node', [scriptPath, '--src', srcRoot, '--dest', destRoot, '--clean']);
      expect(stdout).toContain('Sync complete');

      const copied = normalizeList(await walkFiles(destRoot), destRoot);
      const expected = [
        '_SYNC_LOG.json',
        'ROOT.mdx',
        'SIDEBAR.yaml',
        'image.png',
        'nested/ALLOWED.md',
      ];
      expect(new Set(copied)).toEqual(new Set(expected));
      expect(copied).toHaveLength(expected.length);

      const sidebarCopy = await readFile(path.join(destRoot, 'SIDEBAR.yaml'), 'utf8');
      expect(sidebarCopy).toBe(sidebar);
    } finally {
      await rm(srcRoot, { recursive: true, force: true });
      await rm(destRoot, { recursive: true, force: true });
    }
  });
});
