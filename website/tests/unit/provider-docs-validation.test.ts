import { describe, expect, it } from 'vitest';
import { existsSync } from 'node:fs';
import path from 'node:path';

const repoRoot = path.resolve(__dirname, '..', '..', '..');

const requiredDocs = [
  'docs/providers/README.md',
  'docs/providers/windows.md',
  'docs/providers/linux.md',
  'docs/providers/python.md',
  'docs/providers/troubleshooting.md',
  'docs/developers/aicf-api-quickstart.md',
  'docs/developers/aicf-wallet-funding.md',
  'docs/developers/aicf-contract-jobs.md',
];

describe('provider and developer docs coverage', () => {
  it('includes required provider/developer onboarding docs', () => {
    const missing = requiredDocs.filter((file) => !existsSync(path.join(repoRoot, file)));
    expect(missing).toEqual([]);
  });
});
