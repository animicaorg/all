import { describe, expect, it } from 'vitest';

import { loadProviderDownloadsPageData } from '../../src/features/provider/downloads';

describe('provider download manifest', () => {
  it('loads provider bundles and includes quickstart commands', () => {
    const data = loadProviderDownloadsPageData();

    expect(data.cards.length).toBeGreaterThanOrEqual(3);
    expect(data.cards.some((card) => card.platform === 'windows')).toBe(true);
    expect(data.cards.some((card) => card.platform === 'linux')).toBe(true);
    expect(data.cards.some((card) => card.platform === 'python')).toBe(true);

    const linux = data.cards.find((card) => card.platform === 'linux');
    expect(linux?.quickStart.includes('start-worker')).toBe(true);
  });
});
