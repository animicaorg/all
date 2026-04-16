import { afterEach, describe, expect, it, vi } from 'vitest';

import { createMiningApiClient } from '../../src/features/mining/api';
import type { MiningApiResolution } from '../../src/features/mining/types';

const baseResolution: MiningApiResolution = {
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

describe('createMiningApiClient', () => {
  afterEach(() => {
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });

  it('falls back to the next candidate when the first endpoint fails', async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(
        new Response(JSON.stringify({ error: 'missing' }), {
          status: 404,
          headers: { 'content-type': 'application/json' },
        })
      )
      .mockResolvedValueOnce(
        new Response(JSON.stringify({ network: 'mainnet', algorithm: 'sha3' }), {
          status: 200,
          headers: { 'content-type': 'application/json' },
        })
      );

    vi.stubGlobal('fetch', fetchMock);

    const client = createMiningApiClient({
      resolution: baseResolution,
      currentOrigin: 'https://animica.org',
    });

    const result = await client.fetchConfig();

    expect(result.ok).toBe(true);
    if (result.ok) {
      expect(result.data.network).toBe('mainnet');
      expect(result.meta.url).toBe('https://animica.org/api/mining/config');
    }

    expect(fetchMock.mock.calls[0]?.[0]).toBe('https://pool.animica.org/api/mining/config');
    expect(fetchMock.mock.calls[1]?.[0]).toBe('https://animica.org/api/mining/config');
  });

  it('returns a structured error when every candidate fails', async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(
        new Response(JSON.stringify({ error: 'offline' }), {
          status: 503,
          headers: { 'content-type': 'application/json' },
        })
      )
      .mockResolvedValueOnce(
        new Response(JSON.stringify({ error: 'still offline' }), {
          status: 502,
          headers: { 'content-type': 'application/json' },
        })
      );

    vi.stubGlobal('fetch', fetchMock);

    const client = createMiningApiClient({
      resolution: baseResolution,
      currentOrigin: 'https://animica.org',
    });

    const result = await client.fetchDownloads();

    expect(result.ok).toBe(false);
    if (!result.ok) {
      expect(result.error.code).toBe('http_error');
      expect(result.error.attempts).toHaveLength(2);
      expect(result.error.attempts[0]?.status).toBe(503);
      expect(result.error.attempts[1]?.status).toBe(502);
    }
  });

  it('fetches miners payload successfully', async () => {
    const fetchMock = vi.fn().mockResolvedValueOnce(
      new Response(
        JSON.stringify({
          items: [{ worker_id: 'rig-01', worker_name: 'rig-01' }],
          total: 1,
        }),
        {
          status: 200,
          headers: { 'content-type': 'application/json' },
        }
      )
    );

    vi.stubGlobal('fetch', fetchMock);

    const client = createMiningApiClient({
      resolution: baseResolution,
      currentOrigin: 'https://animica.org',
    });

    const result = await client.fetchMiners();
    expect(result.ok).toBe(true);
    if (result.ok) {
      expect(result.data.items?.[0]?.worker_name).toBe('rig-01');
    }
    expect(fetchMock.mock.calls[0]?.[0]).toBe('https://pool.animica.org/api/miners?page=1&page_size=200');
  });

  it('requests additional miner pages until all miners are loaded', async () => {
    const pageOneItems = Array.from({ length: 200 }, (_, index) => ({
      worker_id: `rig-${index + 1}`,
      worker_name: `rig-${index + 1}`,
      hashrate_1m: 1000 + index,
    }));
    const pageTwoItems = Array.from({ length: 3 }, (_, index) => ({
      worker_id: `rig-${index + 201}`,
      worker_name: `rig-${index + 201}`,
      hashrate_1m: 500 + index,
    }));

    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(
        new Response(
          JSON.stringify({
            items: pageOneItems,
            total: 203,
          }),
          {
            status: 200,
            headers: { 'content-type': 'application/json' },
          }
        )
      )
      .mockResolvedValueOnce(
        new Response(
          JSON.stringify({
            items: pageTwoItems,
            total: 203,
          }),
          {
            status: 200,
            headers: { 'content-type': 'application/json' },
          }
        )
      );

    vi.stubGlobal('fetch', fetchMock);

    const client = createMiningApiClient({
      resolution: baseResolution,
      currentOrigin: 'https://animica.org',
    });

    const result = await client.fetchMiners();
    expect(result.ok).toBe(true);
    if (result.ok) {
      expect(result.data.items).toHaveLength(203);
      expect(result.data.total).toBe(203);
    }

    expect(fetchMock.mock.calls[0]?.[0]).toBe('https://pool.animica.org/api/miners?page=1&page_size=200');
    expect(fetchMock.mock.calls[1]?.[0]).toBe('https://pool.animica.org/api/miners?page=2&page_size=200');
  });
});
