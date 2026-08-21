'use strict';
/**
 * Mesh index tests.
 *
 * The claims that justify charging for this — rather than pointing an agent at
 * two free directories — are: implausible prices are treated as bad data,
 * inflated call counts do not buy rank, the same service listed on four hosts
 * collapses to one, and a directory outage degrades coverage instead of taking
 * the product down. Those are what get tested.
 */

const { test } = require('node:test');
const assert = require('node:assert/strict');

const M = require('../src/products/mesh-index');
const { createMeshFindProduct, createMeshIndexCache } = require('../src/products/mesh');
const { loadGatewayConfig } = require('../src/config');

const cfg = loadGatewayConfig(process.env);

// ---------------------------------------------------------------------------
// Normalisation and merging
// ---------------------------------------------------------------------------

test('canon merges the same resource across directories, and keeps different ones apart', () => {
  assert.equal(M.canon('https://API.Example.com/x/'), M.canon('http://www.api.example.com/x'));
  assert.notEqual(M.canon('https://a.example.com/x'), M.canon('https://b.example.com/x'));
  assert.notEqual(M.canon('https://a.example.com/x'), M.canon('https://a.example.com/y'));
});

test('bazaar prices are decoded from atomic units with the declared decimals', () => {
  const six = M.normalizeBazaar({ resource: 'https://e.com/a', accepts: [{ amount: '7000', extra: { name: 'USD Coin' } }] });
  assert.equal(six.price_usd, 0.007);
  const eighteen = M.normalizeBazaar({ resource: 'https://e.com/b', accepts: [{ amount: '1000000000000000000', extra: { decimals: 18 } }] });
  assert.equal(eighteen.price_usd, 1);
});

test('merge fills gaps without overwriting a value the first source already had', () => {
  const a = { resource: 'r', description: 'short', price_usd: 0.01, calls_30d: 5, sources: ['bazaar'] };
  const b = { resource: 'r', description: 'a much longer and more useful description', price_usd: 9.99, latency_p50_ms: 120, sources: ['402index'] };
  const m = M.merge(a, b);
  assert.equal(m.price_usd, 0.01, 'an existing price is authoritative');
  assert.equal(m.latency_p50_ms, 120, 'a missing field is filled');
  assert.match(m.description, /much longer/, 'the more informative description wins');
  assert.deepEqual(m.sources.sort(), ['402index', 'bazaar']);
});

// ---------------------------------------------------------------------------
// The three claims
// ---------------------------------------------------------------------------

test('an implausible price is bad data, not a bargain and not a warning', () => {
  assert.match(M.priceIssue(10_000_000_000), /outside any plausible/);
  assert.match(M.priceIssue(0), /zero or negative/);
  assert.match(M.priceIssue(null), /no price/);
  assert.equal(M.priceIssue(0.007), null);
});

test('call volume from a handful of payers is discounted, not amplified', () => {
  const organic = M.demandScore({ calls_30d: 1000, unique_payers_30d: 900 });
  const washed = M.demandScore({ calls_30d: 1000, unique_payers_30d: 2 });
  assert.ok(organic.score > washed.score * 5,
    `900 distinct payers must far outrank 2 (${organic.score} vs ${washed.score})`);
  assert.match(washed.note, /concentrated/);
  assert.equal(organic.note, null);
  assert.equal(M.demandScore({ calls_30d: 0, unique_payers_30d: 0 }).score, 0);
});

test('ranking excludes implausible prices and respects a budget', () => {
  const recs = [
    { resource: 'https://a/x', description: 'translate documents fast', price_usd: 0.01, calls_30d: 10, unique_payers_30d: 9, call_spec: { method: 'POST' }, sources: ['bazaar'] },
    { resource: 'https://b/x', description: 'translate documents fast', price_usd: 1e10, calls_30d: 10, unique_payers_30d: 9, call_spec: null, sources: ['bazaar'] },
    { resource: 'https://c/x', description: 'translate documents fast', price_usd: 5, calls_30d: 10, unique_payers_30d: 9, call_spec: null, sources: ['bazaar'] },
  ];
  const bm25 = M.buildBm25(recs);
  const q = M.tokens('translate documents');
  const all = M.rank(recs, bm25, q, { maxPriceUsd: null, requireCallable: false });
  assert.equal(all.scored.length, 2, 'the ten-billion-dollar row is dropped entirely');
  assert.ok(!all.scored.some((s) => s.record.resource === 'https://b/x'));

  const budget = M.rank(recs, bm25, q, { maxPriceUsd: 0.05, requireCallable: false });
  assert.deepEqual(budget.scored.map((s) => s.record.resource), ['https://a/x']);

  const callable = M.rank(recs, bm25, q, { maxPriceUsd: null, requireCallable: true });
  assert.deepEqual(callable.scored.map((s) => s.record.resource), ['https://a/x']);
});

test('the returned weights are the ones that were applied', () => {
  const recs = [{ resource: 'https://a/x', description: 'ocr scanned pdf', price_usd: 0.01, calls_30d: 1, unique_payers_30d: 1, call_spec: null, sources: [] }];
  const r = M.rank(recs, M.buildBm25(recs), M.tokens('ocr pdf'), { maxPriceUsd: null, requireCallable: false, weights: { demand: 0.9 } });
  assert.equal(r.weights.demand, 0.9);
  assert.equal(r.weights.relevance, 0.45, 'unspecified weights keep their defaults');
});

// ---------------------------------------------------------------------------
// End to end against stubbed directories
// ---------------------------------------------------------------------------

function jsonRes(obj) {
  return { ok: true, status: 200, headers: { get: () => 'application/json' }, json: async () => obj };
}

function directories({ bazaar = [], index402 = [], bazaarFails = false } = {}) {
  return async (url) => {
    if (url.startsWith(M.BAZAAR)) {
      if (bazaarFails) throw new Error('bazaar down');
      const off = Number(new URL(url).searchParams.get('offset') || 0);
      return jsonRes({ items: off ? [] : bazaar, pagination: { total: bazaar.length } });
    }
    const off = Number(new URL(url).searchParams.get('offset') || 0);
    return jsonRes({ services: off ? [] : index402, total: index402.length });
  };
}

const MIRRORS = ['https://app-a1.vercel.app/api/img', 'https://app-b2.vercel.app/api/img', 'https://app-c3.vercel.app/api/img'];

test('mirror deployments of one service collapse into a single result', async () => {
  const bazaar = MIRRORS.map((r) => ({
    resource: r, description: 'Generate an image from a text prompt using AI',
    accepts: [{ amount: '11110', network: 'eip155:8453' }],
    quality: { l30DaysTotalCalls: 5, l30DaysUniquePayers: 4 },
  }));
  const p = createMeshFindProduct({ cfg, fetchImpl: directories({ bazaar }) });
  const d = (await p.handler({ params: p.validate({ json: { goal: 'generate an image from a text prompt', limit: 10 } }) })).bodyObj;

  assert.equal(d.results.length, 1, 'three identical listings are one service');
  assert.equal(d.distinct_services, 1);
  assert.equal(d.results[0].also_listed_at.length, 2, 'the other hosts are named, not hidden');
  assert.ok(d.results[0].why.some((w) => /mirrors/.test(w)));
});

test('one directory being down degrades coverage instead of failing the product', async () => {
  const index402 = [{ url: 'https://x.example/ocr', name: 'OCR', description: 'ocr scanned documents', price_usd: '0.02', health_status: 'healthy', latency_p50_ms: '300', reliability_score: '90' }];
  const p = createMeshFindProduct({ cfg, fetchImpl: directories({ index402, bazaarFails: true }) });
  const d = (await p.handler({ params: p.validate({ json: { goal: 'ocr scanned documents' } }) })).bodyObj;

  assert.equal(d.results.length, 1);
  assert.equal(d.index.sources.bazaar.ok, false, 'the outage is reported, not swallowed');
  assert.match(d.index.sources.bazaar.error, /bazaar down/);
  assert.equal(d.index.sources['402index'].ok, true);
});

test('every directory down is unavailable, not an empty result set', async () => {
  const p = createMeshFindProduct({ cfg, fetchImpl: async () => { throw new Error('no net'); } });
  await assert.rejects(
    () => p.handler({ params: p.validate({ json: { goal: 'anything at all' } }) }),
    (e) => { assert.match(String(e.message), /no x402 directory could be reached/); return true; },
  );
});

test('concurrent callers cause one harvest, not one each', async () => {
  let calls = 0;
  const base = directories({ bazaar: [{ resource: 'https://a/x', description: 'translate text', accepts: [{ amount: '1000' }], quality: {} }] });
  const cache = createMeshIndexCache({ cfg, fetchImpl: async (u) => { calls++; return base(u); } });
  await Promise.all([cache.getIndex(), cache.getIndex(), cache.getIndex(), cache.getIndex()]);
  const afterFirst = calls;
  await cache.getIndex();
  assert.equal(calls, afterFirst, 'a warm index issues no further upstream requests');
  assert.ok(afterFirst <= 4, `four concurrent callers must share one harvest, saw ${afterFirst} upstream requests`);
});

test('a goal of only stop words is refused rather than matched against everything', () => {
  const p = createMeshFindProduct({ cfg, fetchImpl: directories({}) });
  assert.throws(() => p.validate({ json: { goal: 'the a of to' } }), /no searchable words/);
  assert.throws(() => p.validate({ json: { goal: 'ok' } }), /goal is required/);
  assert.throws(() => p.validate({ json: { goal: 'translate', limit: 0 } }), /limit must be/);
  assert.throws(() => p.validate({ json: { goal: 'translate', max_price_usd: -1 } }), /positive number/);
});

test('a rate-limited directory is retried with backoff, not abandoned', async () => {
  // We caused a real 429 by harvesting 200 pages back-to-back. Honouring the
  // directory's own signal is what keeps a source from silently vanishing.
  let attempts = 0;
  const fast = loadGatewayConfig({ X402_MESH_PAGE_DELAY_MS: '0' });
  const impl = async (url) => {
    if (url.startsWith(M.BAZAAR)) {
      attempts++;
      if (attempts === 1) {
        return { ok: false, status: 429, headers: { get: (h) => (h === 'retry-after' ? '0' : null) }, json: async () => ({}) };
      }
      const off = Number(new URL(url).searchParams.get('offset') || 0);
      return jsonRes({ items: off ? [] : [{ resource: 'https://a/x', description: 'translate text', accepts: [{ amount: '1000' }], quality: {} }], pagination: { total: 1 } });
    }
    return jsonRes({ services: [], total: 0 });
  };
  const cache = createMeshIndexCache({ cfg: fast, fetchImpl: impl });
  const index = await cache.getIndex();
  assert.equal(index.counts.sources.bazaar.ok, true, 'a 429 must be retried, not treated as the source being down');
  assert.ok(attempts >= 2, 'the retry actually happened');
  assert.equal(index.counts.total, 1);
});

test('a restart reuses the persisted snapshot instead of re-harvesting', async () => {
  // Five deploys in twenty minutes meant five full sweeps of two third-party
  // directories, which is exactly how we earned a 429 and lost a source.
  const { createGatewayStore } = require('../src/store/gateway');
  const st = createGatewayStore(':memory:');
  let harvests = 0;
  const impl = async (url) => {
    if (url.startsWith(M.BAZAAR)) {
      const off = Number(new URL(url).searchParams.get('offset') || 0);
      if (!off) harvests++;
      return jsonRes({ items: off ? [] : [{ resource: 'https://a/x', description: 'translate text', accepts: [{ amount: '1000' }], quality: {} }], pagination: { total: 1 } });
    }
    return jsonRes({ services: [], total: 0 });
  };
  const first = createMeshIndexCache({ cfg, fetchImpl: impl, gatewayStore: st });
  await first.getIndex();
  assert.equal(harvests, 1);

  // A brand-new cache object stands in for a fresh process.
  const afterRestart = createMeshIndexCache({ cfg, fetchImpl: impl, gatewayStore: st });
  const idx = await afterRestart.getIndex();
  assert.equal(harvests, 1, 'a restart within the TTL must not re-harvest');
  assert.equal(idx.records.length, 1, 'and must still serve the full index');
});

test('a corrupt snapshot is a cache miss, not a crash', async () => {
  const { createGatewayStore } = require('../src/store/gateway');
  const st = createGatewayStore(':memory:');
  st.putIndexSnapshot({ harvestedAt: Date.now(), counts: {}, records: [] });
  // Records empty -> treated as unusable, so a real harvest runs.
  let harvested = false;
  const impl = async (url) => {
    if (url.startsWith(M.BAZAAR)) {
      harvested = true;
      const off = Number(new URL(url).searchParams.get('offset') || 0);
      return jsonRes({ items: off ? [] : [{ resource: 'https://a/x', description: 'ocr text', accepts: [{ amount: '1000' }], quality: {} }], pagination: { total: 1 } });
    }
    return jsonRes({ services: [], total: 0 });
  };
  const c = createMeshIndexCache({ cfg, fetchImpl: impl, gatewayStore: st });
  await c.getIndex();
  assert.equal(harvested, true);
});
