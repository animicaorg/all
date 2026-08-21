'use strict';
/**
 * Harvester tests.
 *
 * This is the component that calls 31,000 endpoints belonging to other people,
 * so most of these assert what it must NOT do: never attach a payment, never
 * guess a write verb at an unknown endpoint, never re-probe something that
 * already answered without payment, never burst at one host, and never reach a
 * private address. The parsing tests come second because a bug there costs a
 * wrong field; a bug in the guards costs somebody else money or uptime.
 */

const { test } = require('node:test');
const assert = require('node:assert/strict');

const { createHarvester, parseAccepts, pickAccept, specFrom, atomicToUsd, createHostGate } = require('../src/products/mesh-harvest');
const { createGatewayStore } = require('../src/store/gateway');
const { loadGatewayConfig } = require('../src/config');

const cfg = loadGatewayConfig({ X402_MESH_PROBE_HOST_DELAY_MS: '0' });
const publicLookup = async () => [{ address: '93.184.216.34', family: 4 }];

function res(status, obj, headers = {}) {
  return {
    ok: status >= 200 && status < 300,
    status,
    headers: { get: (h) => headers[h.toLowerCase()] || null },
    text: async () => (typeof obj === 'string' ? obj : JSON.stringify(obj)),
  };
}

const ACCEPTS_402 = {
  x402Version: 2,
  accepts: [{
    scheme: 'exact', network: 'eip155:8453', maxAmountRequired: '12000',
    asset: '0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913',
    payTo: '0xMerchant', maxTimeoutSeconds: 600,
    extra: { name: 'USD Coin', decimals: 6 },
    outputSchema: { input: { method: 'POST', bodyType: 'json', bodyFields: { text: { type: 'string' } }, description: 'summarise text' } },
  }],
};

function store() { return createGatewayStore(':memory:'); }

// ---------------------------------------------------------------------------
// Safety
// ---------------------------------------------------------------------------

test('a probe never attaches a payment header', async () => {
  const seen = [];
  const h = createHarvester({
    cfg, gatewayStore: store(), lookup: publicLookup,
    fetchImpl: async (u, init) => { seen.push(init); return res(402, ACCEPTS_402); },
  });
  await h.probeOne('https://m.example/api');
  for (const init of seen) {
    const keys = Object.keys(init.headers || {}).map((k) => k.toLowerCase());
    assert.ok(!keys.some((k) => k.includes('payment')), `probe sent a payment-ish header: ${keys}`);
  }
});

test('an unknown endpoint is probed with GET before POST, and never with a destructive verb', async () => {
  const verbs = [];
  const h = createHarvester({
    cfg, gatewayStore: store(), lookup: publicLookup,
    fetchImpl: async (u, init) => { verbs.push(init.method); return res(404, { e: 1 }); },
  });
  await h.probeOne('https://m.example/api');
  assert.deepEqual(verbs, ['GET', 'POST'], 'GET is tried first; POST only as a fallback');
  assert.ok(!verbs.some((v) => ['PUT', 'PATCH', 'DELETE'].includes(v)));
});

test('a POST fallback carries an empty body, not invented arguments', async () => {
  let postBody = 'unset';
  const h = createHarvester({
    cfg, gatewayStore: store(), lookup: publicLookup,
    fetchImpl: async (u, init) => {
      if (init.method === 'POST') postBody = init.body;
      return res(init.method === 'POST' ? 402 : 404, init.method === 'POST' ? ACCEPTS_402 : {});
    },
  });
  await h.probeOne('https://m.example/api');
  assert.equal(postBody, '{}', 'guessing arguments at an unknown endpoint could cause a real side effect');
});

test('a resource that answers without payment is recorded as open and never re-probed', async () => {
  const st = store();
  let calls = 0;
  const h = createHarvester({
    cfg, gatewayStore: st, lookup: publicLookup,
    fetchImpl: async () => { calls++; return res(200, { result: 'free data' }); },
  });
  const records = [{ key: 'm.example/api', resource: 'https://m.example/api', call_spec: null }];
  await h.sweep(records);
  const after = calls;
  assert.equal(st.getProbe('m.example/api').outcome, 'open');
  await h.sweep(records);
  assert.equal(calls, after, 're-probing an unpaywalled endpoint is a free call against a real service for no new information');
});

test('a private or loopback target is refused before any socket opens', async () => {
  let called = 0;
  const h = createHarvester({
    cfg, gatewayStore: store(),
    lookup: async () => [{ address: '10.0.0.5', family: 4 }],
    fetchImpl: async () => { called++; return res(402, ACCEPTS_402); },
  });
  const row = await h.probeOne('https://internal.example/api');
  assert.equal(row.outcome, 'blocked');
  assert.equal(called, 0, 'the SSRF guard must run before the request, not after');
});

test('probes to one host are serialised, not burst', async () => {
  let inFlight = 0;
  let peak = 0;
  const gate = createHostGate(0);
  const work = () => gate('same.host', async () => {
    inFlight++; peak = Math.max(peak, inFlight);
    await new Promise((r) => setTimeout(r, 5));
    inFlight--;
  });
  await Promise.all([work(), work(), work(), work()]);
  assert.equal(peak, 1, 'one merchant with many listings must not receive simultaneous probes');
});

test('a host gate failure does not stall the queue behind it', async () => {
  const gate = createHostGate(0);
  const first = gate('h', async () => { throw new Error('boom'); }).catch(() => 'failed');
  const second = gate('h', async () => 'ran anyway');
  assert.equal(await first, 'failed');
  assert.equal(await second, 'ran anyway');
});

// ---------------------------------------------------------------------------
// What a 402 tells us
// ---------------------------------------------------------------------------

test('a 402 yields the merchant\'s own price, terms and request shape', async () => {
  const st = store();
  const h = createHarvester({ cfg, gatewayStore: st, lookup: publicLookup, fetchImpl: async () => res(402, ACCEPTS_402) });
  const row = await h.probeAndStore('https://m.example/api');
  assert.equal(row.outcome, 'paywalled');
  assert.equal(row.price_atomic, '12000');
  assert.equal(row.price_usd, '0.012');
  assert.equal(row.pay_to, '0xMerchant');
  assert.equal(row.network, 'eip155:8453');
  assert.equal(row.max_timeout_s, 600);
  const spec = JSON.parse(row.call_spec_json);
  assert.equal(spec.method, 'POST');
  assert.deepEqual(Object.keys(spec.body_fields), ['text']);
  assert.equal(st.getProbe('m.example/api').price_usd, '0.012', 'the result is persisted');
});

test('older x402 body shapes are still understood', () => {
  assert.ok(parseAccepts({ accepts: [{ a: 1 }] }));
  assert.ok(parseAccepts({ paymentRequirements: [{ a: 1 }] }), 'the older field name must not be silently unreadable');
  assert.ok(parseAccepts({ x402: { accepts: [{ a: 1 }] } }));
  assert.equal(parseAccepts({ accepts: [] }), null);
  assert.equal(parseAccepts(null), null);
});

test('a Base lane is preferred when several are offered', () => {
  const picked = pickAccept([{ network: 'solana:xyz' }, { network: 'eip155:8453', tag: 'base' }]);
  assert.equal(picked.tag, 'base');
  assert.equal(pickAccept([{ network: 'solana:xyz' }]).network, 'solana:xyz', 'the only lane wins by default');
});

test('non-6-decimal assets are decoded with their declared decimals', () => {
  assert.equal(atomicToUsd('12000', { extra: { decimals: 6 } }), 0.012);
  assert.equal(atomicToUsd('1000000000000000000', { extra: { decimals: 18 } }), 1);
  assert.equal(atomicToUsd('7000', {}), 0.007, 'six decimals is the default, matching USDC');
});

test('a 402 with no readable terms is still paywalled, and says what is missing', async () => {
  const h = createHarvester({ cfg, gatewayStore: store(), lookup: publicLookup, fetchImpl: async () => res(402, { nope: true }) });
  const row = await h.probeOne('https://m.example/api');
  assert.equal(row.outcome, 'paywalled');
  assert.equal(row.price_usd, null);
  assert.match(row.error, /published no readable accepts/);
});

test('a timeout is an error observation, not a crash', async () => {
  const h = createHarvester({
    cfg, gatewayStore: store(), lookup: publicLookup,
    fetchImpl: async () => { const e = new Error('t'); e.name = 'TimeoutError'; throw e; },
  });
  const row = await h.probeOne('https://m.example/api');
  assert.equal(row.outcome, 'error');
  assert.equal(row.error, 'timeout');
});

test('a sweep respects its probe budget', async () => {
  const st = store();
  let calls = 0;
  const tight = loadGatewayConfig({ X402_MESH_PROBE_HOST_DELAY_MS: '0', X402_MESH_SWEEP_MAX_PROBES: '3' });
  const h = createHarvester({ cfg: tight, gatewayStore: st, lookup: publicLookup, fetchImpl: async () => { calls++; return res(402, ACCEPTS_402); } });
  const records = Array.from({ length: 20 }, (_, i) => ({ key: `h${i}.example/a`, resource: `https://h${i}.example/a`, call_spec: null }));
  const counts = await h.sweep(records);
  assert.equal(counts.probed, 3, 'the sweep must stop at its configured probe cap');
  assert.ok(calls <= 3);
});

test('a sweep spreads across hosts instead of walking one chain', async () => {
  // Probes to one host are serialised by design. A batch ordered host-by-host
  // therefore spends its whole budget on the first merchant while every other
  // host idles — which is exactly what happened live: 10 probes in 3 minutes.
  const st = store();
  const order = [];
  const tight = loadGatewayConfig({ X402_MESH_PROBE_HOST_DELAY_MS: '0', X402_MESH_SWEEP_MAX_PROBES: '6' });
  const h = createHarvester({
    cfg: tight, gatewayStore: st, lookup: publicLookup,
    fetchImpl: async (u) => { order.push(new URL(u).hostname); return res(402, ACCEPTS_402); },
  });
  // One host with many listings, plus two others.
  const records = [
    ...Array.from({ length: 6 }, (_, i) => ({ key: `big.example/a${i}`, resource: `https://big.example/a${i}`, call_spec: null })),
    { key: 'x.example/a', resource: 'https://x.example/a', call_spec: null },
    { key: 'y.example/a', resource: 'https://y.example/a', call_spec: null },
  ];
  await h.sweep(records);
  const hosts = new Set(order.slice(0, 3));
  assert.ok(hosts.size >= 2, `the first probes must span hosts, saw ${[...order.slice(0, 3)]}`);
  assert.ok(order.includes('x.example') && order.includes('y.example'),
    'small hosts must not be starved behind a large one');
});

test('never-probed entries sort ahead of stale ones without a NaN comparator', async () => {
  const st = store();
  st.putProbe({ key: 'old.example/a', resource: 'https://old.example/a', method: 'GET', outcome: 'error',
    http_status: 500, price_atomic: null, price_usd: null, asset: null, network: null, pay_to: null,
    scheme: null, max_timeout_s: null, call_spec_json: null, accepts_json: null, error: 'x',
    latency_ms: 1, probed_at: 1 });
  const order = [];
  const tight = loadGatewayConfig({ X402_MESH_PROBE_HOST_DELAY_MS: '0', X402_MESH_SWEEP_MAX_PROBES: '2' });
  const h = createHarvester({
    cfg: tight, gatewayStore: st, lookup: publicLookup,
    fetchImpl: async (u) => { order.push(new URL(u).hostname); return res(402, ACCEPTS_402); },
  });
  await h.sweep([
    { key: 'old.example/a', resource: 'https://old.example/a', call_spec: null },
    { key: 'new.example/a', resource: 'https://new.example/a', call_spec: null },
  ]);
  assert.equal(order[0], 'new.example', 'a resource we have never called is the more valuable probe');
});
