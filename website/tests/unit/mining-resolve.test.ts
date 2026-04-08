import { describe, expect, it } from 'vitest';

import { resolveMiningApi } from '../../src/features/mining/resolve';

describe('resolveMiningApi', () => {
  it('prefers ANIMICA_MINING_API_BASE_URL over every other source', () => {
    const resolution = resolveMiningApi({
      currentOrigin: 'https://animica.org',
      currentHostname: 'animica.org',
      env: {
        miningApiBaseUrl: 'https://custom.animica.org///',
        poolUrl: 'https://pool.animica.org',
      },
    });

    expect(resolution.source).toBe('env-mining-api-base');
    expect(resolution.publicBaseUrl).toBe('https://custom.animica.org');
    expect(resolution.requestBases[0]).toEqual({
      kind: 'absolute',
      label: 'resolved-base',
      baseUrl: 'https://custom.animica.org',
    });
  });

  it('falls back to ANIMICA_POOL_URL when mining API base is blank', () => {
    const resolution = resolveMiningApi({
      currentOrigin: 'https://staging.animica.org',
      currentHostname: 'staging.animica.org',
      env: {
        poolUrl: 'https://pool.staging.animica.org/',
      },
    });

    expect(resolution.source).toBe('env-pool-url');
    expect(resolution.publicBaseUrl).toBe('https://pool.staging.animica.org');
  });

  it('defaults animica.org and www.animica.org to the production pool host', () => {
    const resolution = resolveMiningApi({
      currentOrigin: 'https://animica.org',
      currentHostname: 'animica.org',
    });

    expect(resolution.source).toBe('production-default');
    expect(resolution.publicBaseUrl).toBe('https://pool.animica.org');
    expect(resolution.requestBases).toEqual([
      {
        kind: 'absolute',
        label: 'resolved-base',
        baseUrl: 'https://pool.animica.org',
      },
      {
        kind: 'same-origin',
        label: 'same-origin',
      },
    ]);
  });

  it('uses same-origin only when already on pool.animica.org', () => {
    const resolution = resolveMiningApi({
      currentOrigin: 'https://pool.animica.org',
      currentHostname: 'pool.animica.org',
    });

    expect(resolution.source).toBe('current-origin-pool');
    expect(resolution.requestBases).toEqual([
      {
        kind: 'same-origin',
        label: 'same-origin',
      },
    ]);
  });

  it('prefers same-origin proxy first in local dev, then explicit env override', () => {
    const resolution = resolveMiningApi({
      currentOrigin: 'http://127.0.0.1:4321',
      currentHostname: '127.0.0.1',
      env: {
        miningApiBaseUrl: 'http://127.0.0.1:8550/',
      },
    });

    expect(resolution.isLocalDev).toBe(true);
    expect(resolution.requestBases).toEqual([
      {
        kind: 'same-origin',
        label: 'same-origin',
      },
      {
        kind: 'absolute',
        label: 'resolved-base',
        baseUrl: 'http://127.0.0.1:8550',
      },
    ]);
  });
});
