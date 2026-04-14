import { describe, expect, it } from 'vitest';

import {
  normalizeMiningConfig,
  normalizeMiningDownloads,
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
});
