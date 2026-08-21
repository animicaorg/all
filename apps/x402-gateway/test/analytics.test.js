'use strict';
/**
 * x402 Analytics tests.
 *
 * The dangerous failure for an analytics product is not a crash — it is a
 * confident number computed over the wrong rows, or a number a model made up.
 * Merchants set prices on these figures. So the tests that matter are:
 *
 *   - implausible prices never enter a statistic, and the exclusion is counted;
 *   - a thin comparable set produces a REFUSAL, not a percentile;
 *   - the coverage floor keeps unrelated services out of a segment;
 *   - a model that lies cannot change a single figure in the response;
 *   - provenance says "fallback" whenever AICF did not actually serve;
 *   - inference being down degrades the narrative and nothing else.
 */

const { test } = require('node:test');
const assert = require('node:assert/strict');

const {
  createAnalyticsMarketProduct, createAnalyticsPriceProduct, createAnalyticsPeersProduct,
  percentile, priceStats, demandStats, callabilityStats, trendFrom, segmentKey,
} = require('../src/products/analytics');
const { createAicfEngine, groundNumbers } = require('../src/products/aicf');
const { loadGatewayConfig } = require('../src/config');
const M = require('../src/products/mesh-index');

const cfg = loadGatewayConfig({});

function svc(over = {}) {
  const resource = over.resource || 'https://a.example/weather';
  return {
    key: M.canon(resource),
    resource,
    description: 'weather forecast data api',
    price_usd: 0.01,
    asset: 'USDC',
    network: 'eip155:8453',
    pay_to: '0x1',
    calls_30d: 100,
    unique_payers_30d: 50,
    last_called_at: new Date().toISOString(),
    call_spec: { method: 'POST' },
    sources: ['bazaar'],
    ...over,
  };
}

/** A weather segment of N services with a spread of prices. */
function weatherSet(n = 12, priceAt = (i) => (i + 1) / 1000) {
  return Array.from({ length: n }, (_, i) => svc({
    resource: `https://w${i}.example/weather`,
    description: 'weather forecast data api for cities',
    price_usd: priceAt(i),
    calls_30d: (i + 1) * 10,
    unique_payers_30d: (i + 1) * 5,
  }));
}

function stubIndex(records) {
  return {
    getIndex: async () => ({
      at: Date.now(),
      records,
      bm25: M.buildBm25(records),
      counts: { total: records.length, callable: 0, priced: 0, price_rejected: 0, probe: {}, sources: {} },
    }),
  };
}

/** A model that answers with whatever text you give it. */
function modelSaying(text, { model = 'animica-chat', fail = false } = {}) {
  return async (url) => {
    if (fail) throw new Error('connection refused');
    if (String(url).includes('/models')) {
      return { ok: true, status: 200, json: async () => ({ data: [{ id: 'animica-chat', serving: true }] }) };
    }
    return {
      ok: true, status: 200,
      json: async () => ({ model, choices: [{ message: { content: text } }] }),
    };
  };
}

const noNarrative = { narrative: false };

async function market(records, body = {}, fetchImpl = modelSaying('ok.'), store = null) {
  const p = createAnalyticsMarketProduct({ cfg, indexCache: stubIndex(records), gatewayStore: store, fetchImpl });
  const out = await p.handler({ params: p.validate({ json: { ...noNarrative, ...body } }) });
  assert.equal(out.status, 200);
  return out.bodyObj;
}

async function priceCheck(records, body, fetchImpl = modelSaying('ok.')) {
  const p = createAnalyticsPriceProduct({ cfg, indexCache: stubIndex(records), fetchImpl });
  const out = await p.handler({ params: p.validate({ json: { ...noNarrative, ...body } }) });
  assert.equal(out.status, 200);
  return out.bodyObj;
}

async function peers(records, body, fetchImpl = modelSaying('ok.')) {
  const p = createAnalyticsPeersProduct({ cfg, indexCache: stubIndex(records), fetchImpl });
  const out = await p.handler({ params: p.validate({ json: { ...noNarrative, ...body } }) });
  assert.equal(out.status, 200);
  return out.bodyObj;
}

// ---------------------------------------------------------------------------
// The arithmetic
// ---------------------------------------------------------------------------

test('percentile is nearest-rank, so every value returned is a price something charges', () => {
  const v = [0.001, 0.005, 0.01, 0.05, 0.1];
  assert.equal(percentile(v, 50), 0.01);
  assert.equal(percentile(v, 100), 0.1);
  assert.equal(percentile(v, 1), 0.001);
  assert.equal(percentile([], 50), null);
});

test('an implausible price is excluded from every statistic AND counted', () => {
  // The live listing that motivated this advertises $10,000,000,000 a call.
  const s = priceStats([
    svc({ price_usd: 0.01 }), svc({ price_usd: 0.02 }), svc({ price_usd: 0.03 }),
    svc({ price_usd: 10_000_000_000 }),
  ]);
  assert.equal(s.priced, 3);
  assert.equal(s.excluded_as_implausible, 1);
  assert.equal(s.max, 0.03, 'the ten-billion-dollar listing must not become the max');
  assert.equal(s.median, 0.02);
  assert.ok(s.mean < 0.05, 'one bad row must not move the mean');
});

test('an unpriced listing is counted as unpriced, never as a zero price', () => {
  const s = priceStats([svc({ price_usd: null }), svc({ price_usd: 0.02 }), svc({ price_usd: 0.04 })]);
  assert.equal(s.unpriced, 1);
  assert.equal(s.priced, 2);
  assert.equal(s.min, 0.02, 'a missing price must not become the cheapest option');
});

test('demand: a 402index-only listing is not counted as zero demand', () => {
  // "Nobody called it" and "the directory does not publish call counts" are
  // different facts, and only one of them is a market signal.
  const d = demandStats([
    svc({ calls_30d: 100, unique_payers_30d: 100 }),
    svc({ sources: ['402index'], calls_30d: 0, unique_payers_30d: 0 }),
  ]);
  assert.equal(d.services_with_demand_data, 1);
  assert.equal(d.services_called_in_30d, 1);
});

test('demand: payer concentration is reported, so self-calling cannot read as demand', () => {
  const d = demandStats([svc({ calls_30d: 900, unique_payers_30d: 2 })]);
  assert.ok(d.median_payer_concentration > 0.99);
  assert.equal(d.most_concentrated.unique_payers_30d, 2);
});

test('callability share reflects that most of the economy publishes no call spec', () => {
  const c = callabilityStats([svc({ call_spec: null }), svc({ call_spec: null }), svc()]);
  assert.equal(c.with_call_spec, 1);
  assert.equal(c.share_callable, 0.3333);
});

// ---------------------------------------------------------------------------
// Refusals — the part that makes the numbers safe to price against
// ---------------------------------------------------------------------------

test('a thin segment is REFUSED, not given a percentile', async () => {
  const b = await market([...weatherSet(3), svc({ resource: 'https://x.example/pdf', description: 'pdf to text conversion' })],
    { segment: 'weather forecast data' });
  assert.equal(b.sufficient, false);
  assert.equal(b.matched, 3);
  assert.equal(b.price, undefined, 'no distribution may be published for a segment below the floor');
  assert.match(b.reason, /minimum/i);
});

test('the coverage floor keeps an unrelated service out of a segment', async () => {
  // BM25 alone would happily include this: it shares the word "data".
  const records = [...weatherSet(10), svc({ resource: 'https://e.example/mail', description: 'email address validation data service' })];
  const b = await market(records, { segment: 'weather forecast data api' });
  assert.equal(b.sufficient, true);
  assert.equal(b.selection.matched, 10, 'the email validator shares a word but not the segment');
});

test('price positioning is REFUSED below the comparable minimum, and says why', async () => {
  const b = await priceCheck(weatherSet(4), { description: 'weather forecast data api for cities', price_usd: 0.02 });
  assert.equal(b.sufficient, false);
  assert.equal(b.your_position, undefined);
  assert.equal(b.comparables_found, 4);
  assert.ok(b.whole_market_reference, 'a sanity reference is still offered');
  assert.match(b.whole_market_reference.note, /not a comparable set/i);
});

test('a price above the plausible band is rejected at validation, not silently ranked', () => {
  const p = createAnalyticsPriceProduct({ cfg, indexCache: stubIndex([]), fetchImpl: modelSaying('x') });
  assert.throws(() => p.validate({ json: { description: 'a thing', price_usd: 1e9 } }), /plausible/i);
});

test('a resource missing from the index is a finding, not an error', async () => {
  const b = await peers(weatherSet(10), { resource: 'https://nowhere.example/thing' });
  assert.equal(b.found, false);
  assert.match(b.what_this_means, /invisible|cannot find/i);
  assert.ok(b.suggestion);
});

// ---------------------------------------------------------------------------
// The statistics themselves
// ---------------------------------------------------------------------------

test('market: the distribution is computed over the matched segment only', async () => {
  const records = [
    ...weatherSet(10, (i) => (i + 1) / 1000),                       // $0.001 .. $0.010
    svc({ resource: 'https://p.example/pdf', description: 'pdf ocr text extraction', price_usd: 5 }),
  ];
  const b = await market(records, { segment: 'weather forecast data api' });
  assert.equal(b.selection.matched, 10);
  assert.equal(b.price.min, 0.001);
  assert.equal(b.price.max, 0.01);
  assert.equal(b.price.median, 0.005);
  assert.ok(b.price.p90 <= 0.01);
});

test('price: the percentile is the share of comparables at or below your price', async () => {
  // Ten comparables at $0.001 .. $0.010; charging $0.005 puts five at or below.
  const b = await priceCheck(weatherSet(10, (i) => (i + 1) / 1000),
    { description: 'weather forecast data api for cities', price_usd: 0.005 });
  assert.equal(b.sufficient, true);
  assert.equal(b.comparables.count, 10);
  assert.equal(b.your_position.percentile, 50);
  assert.equal(b.your_position.comparables_cheaper_than_you, 4);
  assert.equal(b.your_position.comparables_more_expensive_than_you, 5);
  assert.equal(b.your_position.vs_median.ratio, 1);
  assert.match(b.your_position.reading, /normal band/);
});

test('price: the suggested band is the comparables\' own interquartile range, and says what it cannot know', async () => {
  const b = await priceCheck(weatherSet(12, (i) => (i + 1) / 1000), { description: 'weather forecast data api for cities' });
  assert.equal(b.suggested_band.low_usd, b.distribution.p25);
  assert.equal(b.suggested_band.high_usd, b.distribution.p75);
  assert.match(b.suggested_band.what_this_does_not_know, /settlement cost/);
});

test('price: every comparable the percentile was computed over is listed back', async () => {
  const b = await priceCheck(weatherSet(10), { description: 'weather forecast data api for cities', top_comparables: 25 });
  assert.equal(b.comparables.listed.length, 10);
  for (const c of b.comparables.listed) assert.ok(c.resource && c.price_source);
});

test('price: a probe-corrected price is labelled as the merchant\'s own, not the directory\'s', async () => {
  const set = weatherSet(10);
  set[0].directory_price_usd = 0.25;
  set[0].probe = { outcome: 'paywalled' };
  const b = await priceCheck(set, { description: 'weather forecast data api for cities', top_comparables: 25 });
  const row = b.comparables.listed.find((c) => c.resource === set[0].resource);
  assert.match(row.price_source, /402 challenge/);
  assert.match(row.price_source, /0\.25/);
});

test('peers: identical deployments are collapsed as mirrors, not counted as competitors', async () => {
  const set = weatherSet(10);
  const subject = set[0];
  const mirror = svc({
    resource: 'https://mirror.example/weather',
    description: subject.description,
    price_usd: subject.price_usd,
  });
  const b = await peers([...set, mirror], { resource: subject.resource });
  assert.equal(b.found, true);
  assert.equal(b.mirrors.count, 1);
  assert.ok(!b.peers.listed.some((p) => p.resource === mirror.resource), 'a mirror must not appear as a peer');
});

test('peers: ranks are withheld when there are too few peers to rank against', async () => {
  const set = weatherSet(3);
  const b = await peers(set, { resource: set[0].resource });
  assert.equal(b.peers.sufficient, false);
  assert.equal(b.ranks, null);
  assert.match(b.peers.insufficient_reason, /below the/);
});

test('peers: price rank counts how many peers are cheaper', async () => {
  const set = weatherSet(12, (i) => (i + 1) / 1000);   // subject is $0.001, the cheapest
  const b = await peers(set, { resource: set[0].resource });
  assert.equal(b.peers.sufficient, true);
  assert.equal(b.ranks.price.cheaper_than_peers.rank, 1);
  assert.equal(b.subject.price_usd, 0.001);
});

// ---------------------------------------------------------------------------
// Trend
// ---------------------------------------------------------------------------

test('one snapshot yields insufficient_history, never an invented direction', () => {
  const t = trendFrom([{ at: 1, services: 5 }], { at: 2, services: 9 });
  assert.equal(t.available, false);
  assert.equal(t.reason, 'insufficient_history');
  assert.equal(t.services, undefined);
});

test('two snapshots yield a real delta', () => {
  const t = trendFrom(
    [{ at: 2000, services: 10, median_price_usd: 0.02, calls_30d: 5, unique_payers_30d: 3, callable: 1 },
      { at: 1000, services: 8, median_price_usd: 0.01, calls_30d: 4, unique_payers_30d: 2, callable: 1 }],
    { at: 3000, services: 12, median_price_usd: 0.03, calls_30d: 6, unique_payers_30d: 4, callable: 2 },
  );
  assert.equal(t.available, true);
  assert.equal(t.services.then, 8);
  assert.equal(t.services.now, 12);
  assert.equal(t.services.change, 4);
  assert.equal(t.median_price_usd.change_pct, 2);
});

test('segment keys separate different filter sets, so histories cannot be mixed', () => {
  const a = segmentKey('weather data', { network: null, require_callable: false, max_price_usd: null });
  const b = segmentKey('data weather', { network: null, require_callable: false, max_price_usd: null });
  const c = segmentKey('weather data', { network: 'base', require_callable: false, max_price_usd: null });
  assert.equal(a, b, 'word order must not fragment a segment\'s history');
  assert.notEqual(a, c, 'a filtered segment is a different population and needs its own series');
});

test('market records a snapshot, and the rate limit stops a busy segment logging every request', async () => {
  const writes = [];
  const store = {
    marketHistory: () => [],
    recordMarketSnapshot: (s) => { writes.push(s); return { recorded: true }; },
  };
  await market(weatherSet(10), { segment: 'weather forecast data api' }, modelSaying('ok.'), store);
  assert.equal(writes.length, 1);
  assert.ok(writes[0].minIntervalMs > 0, 'the store must be told to rate-limit');
  assert.equal(writes[0].stats.services, 10);
});

test('a history write that throws cannot fail a paid call', async () => {
  const store = {
    marketHistory: () => { throw new Error('db gone'); },
    recordMarketSnapshot: () => { throw new Error('db gone'); },
  };
  const b = await market(weatherSet(10), { segment: 'weather forecast data api' }, modelSaying('ok.'), store);
  assert.equal(b.sufficient, true);
  assert.equal(b.trend.available, false);
});

// ---------------------------------------------------------------------------
// AICF: provenance and the grounding guard
// ---------------------------------------------------------------------------

test('provenance says aicf ONLY when the bridge echoes the requested network model', async () => {
  const e = createAicfEngine({ cfg, fetchImpl: modelSaying('The market is competitive.', { model: 'animica-chat' }) });
  const n = await e.narrate({ instruction: 'x', facts: { a: 1 } });
  assert.equal(n.provenance.network, 'aicf');
  assert.equal(n.provenance.served_by, 'animica-chat');
  assert.match(n.provenance.note, /paid in ANM/);
});

test('provenance says fallback when a different model answered — the measured live case', async () => {
  // Measured on this host 2026-08-19: asking for `animica-chat` returned
  // `anm-fast-8b`, i.e. no AICF worker claimed the job and the pool answered.
  const e = createAicfEngine({ cfg, fetchImpl: modelSaying('The market is competitive.', { model: 'anm-fast-8b' }) });
  const n = await e.narrate({ instruction: 'x', facts: { a: 1 } });
  assert.equal(n.provenance.network, 'fallback');
  assert.equal(n.provenance.served_by, 'anm-fast-8b');
  assert.match(n.provenance.note, /no AICF worker served/);
});

test('AICF being unreachable degrades the narrative and nothing else', async () => {
  const e = createAicfEngine({ cfg, fetchImpl: modelSaying('', { fail: true }) });
  const n = await e.narrate({ instruction: 'x', facts: { a: 1 } });
  assert.equal(n.text, null);
  assert.equal(n.provenance.network, 'unavailable');
  assert.match(n.unavailable_reason, /did not answer/);
});

test('a sentence asserting a number that is not in the facts is deleted', () => {
  const facts = { median: 0.01, services: 200 };
  const g = groundNumbers('The median is 0.01 dollars. There are 4,318 services in this segment. That is 200 listings.', facts);
  assert.equal(g.dropped.length, 1);
  assert.match(g.dropped[0].sentence, /4,318/);
  assert.ok(!g.text.includes('4,318'));
  assert.ok(g.text.includes('0.01'));
});

test('a ratio restated as a percentage is grounded, not deleted', () => {
  // 0.183 reported as "18.3%" is a correct restatement; rejecting it would
  // make every summary useless.
  const g = groundNumbers('Only 18.3% of these can be called.', { share_callable: 0.183 });
  assert.equal(g.dropped.length, 0);
});

test('a model that invents figures cannot change ANY number in the response', async () => {
  const liar = modelSaying('The median price is $999.99 and there are 4,318 services with 87% growth.');
  const honest = await market(weatherSet(10), { segment: 'weather forecast data api', narrative: true }, modelSaying('Prices are tight.'));
  const lied = await market(weatherSet(10), { segment: 'weather forecast data api', narrative: true }, liar);
  assert.deepEqual(lied.price, honest.price, 'computed statistics must be identical regardless of what the model said');
  assert.equal(lied.inference.narrative, null, 'every invented sentence is dropped, leaving nothing');
  assert.ok(lied.inference.grounding.sentences_dropped >= 1);
});

test('the narrative is opt-out, and opting out never touches the statistics', async () => {
  const withOut = await market(weatherSet(10), { segment: 'weather forecast data api', narrative: false });
  assert.equal(withOut.inference.narrative, null);
  assert.equal(withOut.inference.provenance.network, 'not_requested');
  assert.equal(withOut.price.median, 0.005);
});

test('health reports which tiers the bridge says are serving, and never throws', async () => {
  const e = createAicfEngine({ cfg, fetchImpl: modelSaying('x') });
  const h = await e.health();
  assert.equal(h.available, true);
  assert.deepEqual(h.serving, ['animica-chat']);
  const dead = createAicfEngine({ cfg, fetchImpl: modelSaying('', { fail: true }) });
  assert.equal((await dead.health()).available, false);
});

// ---------------------------------------------------------------------------
// Validation
// ---------------------------------------------------------------------------

test('validation rejects a segment made entirely of stop words', async () => {
  const p = createAnalyticsMarketProduct({ cfg, indexCache: stubIndex(weatherSet(10)), fetchImpl: modelSaying('x') });
  await assert.rejects(
    () => p.handler({ params: p.validate({ json: { segment: 'the a of to in' } }) }),
    /no searchable words/,
  );
});

test('validation rejects a non-http resource for the peers endpoint', () => {
  const p = createAnalyticsPeersProduct({ cfg, indexCache: stubIndex([]), fetchImpl: modelSaying('x') });
  assert.throws(() => p.validate({ json: { resource: 'file:///etc/passwd' } }), /http\(s\)/);
  assert.throws(() => p.validate({ json: { resource: 'not a url' } }), /http\(s\)/);
});

test('all three products are priced, enabled and declare their routes', () => {
  const deps = { cfg, indexCache: stubIndex([]), fetchImpl: modelSaying('x') };
  for (const p of [createAnalyticsMarketProduct(deps), createAnalyticsPriceProduct(deps), createAnalyticsPeersProduct(deps)]) {
    assert.ok(Number(p.priceUsd) > 0, `${p.id} must carry a price`);
    assert.equal(p.mode, 'execute-then-settle');
    assert.equal(p.routes.length, 1);
    assert.ok(p.outputSchema.input.bodyFields, `${p.id} must publish its request shape`);
  }
});
