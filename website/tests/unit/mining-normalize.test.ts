import { describe, expect, it } from 'vitest';

import {
  normalizeMiningConfig,
  normalizeMiningDownloads,
  unwrapPoolSummary,
} from '../../src/features/mining/normalize';
import type { MiningApiResolution } from '../../src/features/mining/types';

const productionResolution: MiningApiResolution = {
  currentOrigin: 'https://animica.org',
  currentHostname: 'animica.org',
  source: 'production-default',
  isLocalDev: false,
  publicBaseUrl: 'https://pool.animica.org',
  publicPoolHost: 'pool.animica.org',
  requestBases: [
    {
      kind: 'absolute',
      label: 'resolved-base',
      baseUrl: 'https://pool.animica.org',
    },
    {
      kind: 'same-origin',
      label: 'same-origin',
    },
  ],
  diagnostics: [],
};

describe('mining normalization', () => {
  it('rewrites local-only stratum hosts to the public pool host in production', () => {
    const config = normalizeMiningConfig({
      config: {
        network: 'mainnet',
        algorithm: 'sha3',
        device_type: 'cpu',
        stratum_host: '127.0.0.1',
        stratum_port: 3333,
        stratum_scheme: 'stratum+tcp',
        stratum_url: 'stratum+tcp://127.0.0.1:3333',
      },
      resolution: productionResolution,
    });

    expect(config.stratumHost).toBe('pool.animica.org');
    expect(config.stratumUrl).toBe('stratum+tcp://pool.animica.org:3333');
    expect(config.warnings[0]).toContain('local-only stratum host');
  });

  it('rewrites local-only download URLs and resolves relative manifest URLs', () => {
    const downloads = normalizeMiningDownloads(
      {
        items: [
          {
            platform: 'windows',
            label: 'Windows',
            url: 'http://127.0.0.1:8550/api/mining/downloads/windows',
          },
          {
            platform: 'linux',
            label: 'Ubuntu / Linux',
            url: '/api/mining/downloads/linux',
          },
        ],
      },
      productionResolution
    );

    expect(downloads[0]?.normalizedUrl).toBe('https://pool.animica.org/api/mining/downloads/windows');
    expect(downloads[1]?.normalizedUrl).toBe('https://pool.animica.org/api/mining/downloads/linux');
    expect(downloads[0]?.entrypoint).toBe('animica-miner.exe');
    expect(downloads[1]?.entrypoint).toBe('animica-miner');
  });

  it('maps live pool summary aliases into canonical status fields', () => {
    const summary = unwrapPoolSummary({
      network: 'mainnet',
      num_miners: 11,
      num_workers: 17,
      last_block_hash: '0xabc123',
      payout_interval_seconds: 120,
      payout_countdown_seconds: 30,
      latest_block: {
        height: 4242,
        hash: '0xfeedbeef',
      },
    });

    expect(summary.miners).toBe(11);
    expect(summary.workers).toBe(17);
    expect(summary.latest_block).toBe('0xfeedbeef');
    expect(summary.payout_interval_seconds).toBe(120);
    expect(summary.payout_countdown_seconds).toBe(30);
  });
});
