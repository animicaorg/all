import { describe, it, expect } from 'vitest';
import fs from 'node:fs';
import path from 'node:path';

/**
 * Chain list integrity tests for website/chains/*
 *
 * Validates:
 *  - chains/index.json structure
 *  - referenced chain files exist
 *  - ids and filenames are unique
 *  - minimal required fields exist
 *  - for testnet/localnet, RPC URLs look valid (http/https)
 */

type ChainIndexEntry = {
  id: string;
  name: string;
  file: string;
};

type ChainIndex = {
  chains: ChainIndexEntry[];
};

type ChainFile = {
  id?: string;
  name?: string;
  chainId?: number;
  rpcUrls?: string[];
  explorerUrl?: string;
  status?: string;
  [key: string]: unknown;
};

// Try to locate the website root by walking up until we find chains/index.json
function findWebsiteRoot(start = process.cwd()): string {
  let dir = start;
  // Also consider when tests are run from monorepo root (../website)
  const candidates = [
    dir,
    path.join(dir, 'website'),
    path.join(path.dirname(dir), 'website'),
  ];

  for (const c of candidates) {
    const p = path.join(c, 'chains', 'index.json');
    if (fs.existsSync(p)) return c;
  }

  // Walk up a few levels as a fallback
  for (let i = 0; i < 5; i++) {
    const p = path.join(dir, 'chains', 'index.json');
    if (fs.existsSync(p)) return dir;
    dir = path.dirname(dir);
  }

  throw new Error('Could not locate website root containing chains/index.json');
}

function readJSON<T = any>(p: string): T {
  const raw = fs.readFileSync(p, 'utf8');
  return JSON.parse(raw) as T;
}

function isHttpUrl(u: string): boolean {
  try {
    const url = new URL(u);
    return url.protocol === 'http:' || url.protocol === 'https:';
  } catch {
    return false;
  }
}

describe('chains registry integrity', () => {
  const root = findWebsiteRoot();
  const chainsDir = path.join(root, 'chains');
  const indexPath = path.join(chainsDir, 'index.json');
  const index = readJSON<ChainIndex>(indexPath);

  it('index.json has a non-empty chains array', () => {
    expect(index).toBeTruthy();
    expect(Array.isArray(index.chains)).toBe(true);
    expect(index.chains.length).toBeGreaterThan(0);
  });

  it('entries have unique ids and files, and files exist', () => {
    const ids = new Set<string>();
    const files = new Set<string>();

    for (const entry of index.chains) {
      expect(entry.id, 'entry.id present').toBeTruthy();
      expect(entry.name, 'entry.name present').toBeTruthy();
      expect(entry.file, 'entry.file present').toBeTruthy();

      // ids unique
      expect(ids.has(entry.id)).toBe(false);
      ids.add(entry.id);

      // files unique
      expect(files.has(entry.file)).toBe(false);
      files.add(entry.file);

      // file exists
      const fp = path.join(chainsDir, entry.file);
      const exists = fs.existsSync(fp);
      expect(exists, `missing chain file: ${entry.file}`).toBe(true);
    }
  });

  it('chain files minimally align and have sane fields', () => {
    for (const entry of index.chains) {
      const fp = path.join(chainsDir, entry.file);
      const cf = readJSON<ChainFile>(fp);

      // Minimal alignment
      expect(cf.name ?? entry.name).toBeTruthy();
      // If chain file specifies id, it must match registry id
      if (cf.id) {
        expect(cf.id).toBe(entry.id);
      }

      // If chainId present, must be a positive integer
      if (typeof cf.chainId !== 'undefined') {
        expect(Number.isInteger(cf.chainId)).toBe(true);
        expect(cf.chainId! >= 1).toBe(true);
      }

      // If rpcUrls present, must be non-empty and http(s)
      if (Array.isArray(cf.rpcUrls)) {
        expect(cf.rpcUrls.length).toBeGreaterThan(0);
        for (const u of cf.rpcUrls) {
          expect(isHttpUrl(u)).toBe(true);
        }
      }

      // If explorerUrl present, must be http(s)
      if (typeof cf.explorerUrl === 'string') {
        expect(isHttpUrl(cf.explorerUrl)).toBe(true);
      }
    }
  });

  it('testnet/localnet entries expose at least one HTTP RPC URL (unless explicitly reserved)', () => {
    for (const entry of index.chains) {
      const isDev =
        /testnet|devnet|localnet/i.test(entry.id) || /testnet|devnet|localnet/i.test(entry.name);
      if (!isDev) continue;

      const cf = readJSON<ChainFile>(path.join(chainsDir, entry.file));
      const reserved = cf.status && /reserved|future/i.test(String(cf.status));

      if (reserved) continue;

      expect(Array.isArray(cf.rpcUrls), `${entry.id} should provide rpcUrls`).toBe(true);
      expect((cf.rpcUrls ?? []).length, `${entry.id} should include at least one rpc URL`).toBeGreaterThan(0);
      for (const u of cf.rpcUrls ?? []) {
        expect(isHttpUrl(u), `${entry.id} rpcUrl must be http(s): ${u}`).toBe(true);
      }
    }
  });

  it('no dangling files: every file referenced in index is within chains/ and not outside', () => {
    for (const entry of index.chains) {
      const normalized = path.normalize(entry.file);
      // Prevent path escapes like ../
      expect(normalized.startsWith('..')).toBe(false);
      expect(path.isAbsolute(normalized)).toBe(false);
    }
  });
});
