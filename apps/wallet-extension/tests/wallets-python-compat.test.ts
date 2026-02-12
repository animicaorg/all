import { describe, it, expect } from 'vitest';
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { parseWalletsJson } from '../src/core/wallets/import';

describe('python canonical wallets.json compatibility', () => {
  it('parses canonical fixture exported by python module', () => {
    const fixture = resolve(process.cwd(), '..', '..', 'tests', 'fixtures', 'wallets', 'canonical_v2.json');
    const raw = readFileSync(fixture, 'utf-8');
    const accounts = parseWalletsJson(raw);
    expect(accounts.length).toBeGreaterThan(0);
    expect(accounts[0].label).toBe('alice');
  });
});
