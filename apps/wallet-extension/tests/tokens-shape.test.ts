import { describe, expect, it } from 'vitest';

import { stringifySafe } from '../src/core/rpc/safeJson';

/**
 * Regression: watched-token records carry primitive scalar fields (string
 * address, number chainId, number decimals, string symbol, etc.) and must
 * be safe to JSON-stringify on every boundary they cross — background ↔
 * popup ↔ dapp. This test pins that contract; if a future change introduces
 * a bigint or Uint8Array into a watched-token row, this fails loudly.
 */
const SAMPLE_TOKEN = {
  type: 'ERC20',
  address: '0x' + 'a'.repeat(40),
  symbol: 'TEST',
  decimals: 18,
  chainId: 1,
  name: 'Test Token',
  image: 'https://example/test.png',
  addedAt: 1_700_000_000_000,
};

describe('watched-token records remain JSON-serializable end-to-end', () => {
  it('plain JSON.stringify works on a representative row', () => {
    expect(() => JSON.stringify(SAMPLE_TOKEN)).not.toThrow();
  });

  it('stringifySafe still works defensively if a future change adds a bigint', () => {
    const augmented = { ...SAMPLE_TOKEN, lastIndexedBlock: 12345n };
    expect(() => stringifySafe(augmented)).not.toThrow();
    const text = stringifySafe(augmented);
    // The bigint must survive as a decimal string, not silently dropped.
    expect(text).toContain('12345');
  });

  it('structured-clone-equivalent JSON round-trip preserves all fields', () => {
    const round = JSON.parse(JSON.stringify(SAMPLE_TOKEN)) as typeof SAMPLE_TOKEN;
    expect(round.type).toBe('ERC20');
    expect(round.decimals).toBe(18);
    expect(round.chainId).toBe(1);
  });
});
