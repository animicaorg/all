import { describe, it, expect } from 'vitest';
import path from 'node:path';
import { promises as fs } from 'node:fs';
import { repoRoot } from './utils/docsTestUtils';

describe('Docs-only fast path policy', () => {
  it('asserts governance policy requires docs-only label for docs paths', async () => {
    const policyPath = path.join(repoRoot, 'governance', 'registries', 'module_owners.yaml');
    const content = await fs.readFile(policyPath, 'utf8');

    const hasLabelRule = /id:\s*docs-fastpath[\s\S]*required_labels:\s*\[[^\]]*docs-only[^\]]*\]/m.test(content);
    const hasAppliesTo =
      /id:\s*docs-fastpath[\s\S]*applies_to:\s*\[[^\]]*docs\/\*\*[^\]]*website\/src\/docs\/\*\*[^\]]*website\/content\/\*\*/m.test(
        content,
      );

    expect(hasLabelRule).toBe(true);
    expect(hasAppliesTo).toBe(true);
  });
});
