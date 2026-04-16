import { describe, expect, it } from 'vitest';

import {
  extractMinerItems,
  parseHashrateValue,
  rankMiners,
  resolveMinerHashrate,
} from '../../src/features/mining/miners';

describe('mining miner helpers', () => {
  it('parses hashrate values in numeric and unit formats', () => {
    expect(parseHashrateValue(12_500)).toBe(12_500);
    expect(parseHashrateValue('12,500')).toBe(12_500);
    expect(parseHashrateValue('12.5 kH/s')).toBe(12_500);
    expect(parseHashrateValue('2 MH/s')).toBe(2_000_000);
    expect(parseHashrateValue('1.2e3')).toBe(1_200);
    expect(parseHashrateValue('unknown')).toBeUndefined();
  });

  it('resolves miner hashrate from preferred fields in order', () => {
    expect(resolveMinerHashrate({ hashrate_1m: 0, hashrate_15m: 999 })).toBe(0);
    expect(resolveMinerHashrate({ hashrate_15m: '3.5 kH/s' })).toBe(3_500);
    expect(resolveMinerHashrate({ hashrate_hps: '4.2 KH/s' })).toBe(4_200);
    expect(resolveMinerHashrate({})).toBe(0);
  });

  it('ranks miners by hashrate, then blocks, then accepted shares', () => {
    const ranked = rankMiners([
      {
        worker_name: 'gamma',
        hashrate_1m: 10_000,
        blocks_found: 1,
        shares_accepted: 10,
      },
      {
        worker_name: 'alpha',
        hashrate_1m: 20_000,
        blocks_found: 0,
        shares_accepted: 2,
      },
      {
        worker_name: 'beta',
        hashrate_1m: 10_000,
        blocks_found: 2,
        shares_accepted: 5,
      },
    ]);

    expect(ranked.map((item) => item.worker_name)).toEqual(['alpha', 'beta', 'gamma']);
  });

  it('extracts miners from common payload shapes', () => {
    expect(extractMinerItems({ items: [{ worker_id: 'rig-01' }] })).toHaveLength(1);
    expect(extractMinerItems({ miners: [{ worker_id: 'rig-02' }] })).toHaveLength(1);
    expect(extractMinerItems([{ worker_id: 'rig-03' }])).toHaveLength(1);
    expect(extractMinerItems({ items: ['bad'] })).toHaveLength(0);
  });
});
