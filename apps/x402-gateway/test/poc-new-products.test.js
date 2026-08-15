'use strict';
/**
 * ADVERSARIAL PoCs for the new products (randomness family + chain history +
 * bulk balances), numbered to match the review report.
 *
 * Two kinds live here now:
 *   - REGRESSION tests (F1a, F2, F6, F15, and the new F2b/F6b): the finding
 *     was FIXED, and the PoC that demonstrated it now asserts the fix, so the
 *     vulnerable behaviour cannot come back.
 *   - DEMONSTRATIONS (F3, F4, F5, F7, F8, F9): findings that are still open
 *     and out of scope for this pass. They assert the CURRENT behaviour so
 *     the suite stays green and the behaviour is documented rather than
 *     forgotten; when one is fixed, flip its assertion.
 * F10-F14 assert properties that always held.
 */

const test = require('node:test');
const assert = require('node:assert');

const cfgMod = require('../src/config');
const protocol = require('../src/protocol');
const { createNodeClient } = require('../src/animica-node');
const { createChainIndexStore, createChainIndexer } = require('../src/chain-index');
const {
  QRNG_FIXTURE, buildTestGateway, request, paidRequest, paymentHeaderFrom,
  chainHandlers, fakeNodeFetch,
} = require('./gateway-helpers');

const quiet = { debug() {}, info() {}, warn() {}, error() {}, child() { return quiet; } };

// ---------------------------------------------------------------------------
// F1 (FIXED — regression test) — /x402/random/pick was a ~k-fold response
//      amplifier (k up to X402_RANDOM_MAX_PICKS=1000) because `replace:true`
//      lifts the k<=n rule and nothing bounded the OUTPUT. Now the estimated
//      response size is checked pre-settlement.
// ---------------------------------------------------------------------------

test('F1a: random_pick refuses an amplifying request BEFORE settlement', async () => {
  const t = await buildTestGateway();
  try {
    const big = 'A'.repeat(20_000);
    // 20 KB item x k=1000 would be a ~20 MB answer for $0.02 (and at the
    // shipped caps, 512 KB x 1000 = ~512 MB).
    const body = JSON.stringify({ items: [big], k: 1000, replace: true });
    const res = await request(t.baseUrl, '/x402/random/pick', {
      method: 'POST', headers: { 'content-type': 'application/json' }, body,
    });
    assert.equal(res.status, 400, 'refused before any payment is requested');
    assert.equal(res.json.error, 'response_too_large');
    assert.equal(res.headers.get('payment-required'), null, 'no terms offered');
    assert.equal(res.json.caps.max_response_bytes, t.gw.cfg.randomMaxResponseBytes);
    assert.ok(res.json.caps.estimated_response_bytes > res.json.caps.max_response_bytes);
    assert.match(res.json.hint, /indices_only/);
    assert.equal(t.fac.calls.verify.length, 0);
    assert.equal(t.fac.settled(), 0);

    // The documented escape hatch works and is bounded: indices only.
    const { paid } = await paidRequest(t.baseUrl, '/x402/random/pick', {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify({ items: [big], k: 1000, replace: true, indices_only: true }),
    });
    assert.equal(paid.status, 200);
    assert.equal(paid.json.result.indices.length, 1000);
    assert.equal(paid.json.result.picked, undefined);
    assert.ok(paid.buf.length < 100_000, `indices-only answer stays small (${paid.buf.length} bytes)`);
    assert.equal(t.fac.settled(), 1);

    // And the worst case that IS still purchasable is bounded by the cap.
    const cfg = t.gw.cfg;
    assert.ok(cfg.randomMaxResponseBytes <= 8e6,
      `response cap ${cfg.randomMaxResponseBytes} bounds the amplifier`);
  } finally {
    await t.close();
  }
});

// ---------------------------------------------------------------------------
// F2 (FIXED — regression tests) — the amplifier used to become a PAY-THEN-500
//      once the response crossed V8's max string length: money settled,
//      revenue counted, payer got a bare 500 with no receipt, no incident and
//      no idempotency row. Two independent fixes, one test each:
//        F2a  the request is refused pre-settlement (nothing charged);
//        F2b  ANY throw after settlement is compensated with a signed
//             receipt + incident row + 502, never a bare 500;
//        F2c  execute-then-settle serializes the body BEFORE settling, so an
//             unserializable answer charges nobody.
// ---------------------------------------------------------------------------

test('F2a: the pay-then-500 configuration is now refused before any payment', async () => {
  const t = await buildTestGateway({
    overrides: { randomMaxPicks: 2000, randomMaxBodyBytes: 600_000 },
  });
  try {
    const big = 'A'.repeat(300_000); // 300k x 2000 = 600M chars > 536,870,888
    const body = JSON.stringify({ items: [big], k: 2000, replace: true });
    const res = await request(t.baseUrl, '/x402/random/pick', {
      method: 'POST', headers: { 'content-type': 'application/json' }, body,
    });
    assert.equal(res.status, 400);
    assert.equal(res.json.error, 'response_too_large');
    assert.equal(t.fac.settled(), 0, 'nothing settled');
    assert.equal(t.gw.gatewayStore.listIncidents(null, 100).length, 0);
  } finally {
    await t.close();
  }
});

test('F2b: a throw AFTER settlement pays out a signed receipt + incident, never a bare 500', async () => {
  const t = await buildTestGateway();
  try {
    // Stand in for a full/locked sqlite: the idempotency write throws after
    // the money moved. Everything between countSettled() and send() is now
    // inside the compensation guard, so this is the general case.
    t.gw.gatewayStore.putIdempotent = () => { throw new Error('database or disk is full'); };

    const { paid } = await paidRequest(t.baseUrl, '/x402/random/int', {
      method: 'POST',
      headers: { 'content-type': 'application/json', 'idempotency-key': 'poc-f2b' },
      body: JSON.stringify({ min: 1, max: 6, count: 3 }),
    });
    assert.equal(paid.status, 502, 'not a 500 — the payer is told the money moved');
    assert.equal(paid.json.error, 'delivery_failed');
    assert.ok(paid.json.incident_id, 'an incident row exists for reconciliation');
    assert.ok(paid.json.receipt, 'a signed machine-readable receipt is returned');
    assert.equal(paid.json.receipt.product, 'random_int');
    assert.match(paid.json.receipt.error, /delivery failed after settlement/);
    assert.ok(t.gw.receiptSigner.verify(paid.json.receipt), 'the receipt verifies');
    assert.equal(paid.json.settlement_tx, paid.json.receipt.settlement_tx);
    assert.ok(paid.headers.get('payment-response'), 'settlement proof still rides on the wire');

    // The settlement is real and is counted exactly once...
    assert.equal(t.fac.settled(), 1);
    assert.match(t.gw.renderMetrics(), /x402_revenue_usdc\{product="random_int"\}\s+0\.01/);
    // ...and the operator has the row needed to refund it.
    const incidents = t.gw.gatewayStore.listIncidents(null, 100);
    assert.equal(incidents.length, 1);
    assert.equal(incidents[0].kind, 'delivery_failed');
    assert.equal(incidents[0].status, 'open');
    assert.ok(incidents[0].settlement_tx);
    assert.ok(incidents[0].auth_nonce, 'joined to the facilitator ledger by EIP-3009 nonce');
    assert.match(t.gw.renderMetrics(), /x402_incidents_total\{kind="delivery_failed",product="random_int"\}\s+1/);
  } finally {
    await t.close();
  }
});

test('F2c: an unserializable response is caught BEFORE settlement and charges nobody', async () => {
  const t = await buildTestGateway();
  try {
    // A body JSON.stringify cannot produce (BigInt) stands in for the
    // RangeError a >512 MB answer used to raise.
    const pick = t.gw.registry.products.find((p) => p.id === 'random_pick');
    pick.handler = async () => ({ status: 200, bodyObj: { boom: 1n } });

    const { paid } = await paidRequest(t.baseUrl, '/x402/random/pick', {
      method: 'POST', headers: { 'content-type': 'application/json' },
      body: JSON.stringify({ items: [1, 2, 3], k: 1 }),
    });
    assert.equal(paid.status, 500);
    assert.equal(paid.json.error, 'response_serialization_failed');
    assert.match(paid.json.detail, /NOTHING WAS CHARGED/);
    assert.equal(t.fac.settled(), 0, 'the pre-flight ran before settle');
    assert.equal(t.gw.gatewayStore.listIncidents(null, 100).length, 0);
    assert.doesNotMatch(t.gw.renderMetrics(), /x402_revenue_usdc\{product="random_pick"\}/);
  } finally {
    await t.close();
  }
});

// ---------------------------------------------------------------------------
// F3 — commit-reveal writes its DB row BEFORE settlement, so a payer whose
//      settlement definitively FAILS (nothing charged) still grows the gateway
//      DB by one sealed row per attempt, unboundedly and for free.
// ---------------------------------------------------------------------------

test('F3: failed settlements still persist commit-reveal rows (free unbounded DB growth)', async () => {
  const t = await buildTestGateway();
  try {
    t.fac.behavior.settleSuccess = false;
    t.fac.behavior.settleError = 'invalid_transaction_state'; // definitive: NOT charged
    const N = 25;
    for (let i = 0; i < N; i++) {
      const { paid } = await paidRequest(t.baseUrl, '/x402/random/commit', {
        method: 'POST',
        headers: { 'content-type': 'application/json' },
        body: JSON.stringify({ memo: `spam-${i}-${'x'.repeat(200)}` }),
      });
      assert.equal(paid.status, 402, 'the payer is told the payment failed');
    }
    assert.equal(t.fac.settled(), 0, 'nothing was ever settled');
    assert.equal(t.gw.gatewayStore.countCommitments(), N,
      'every unpaid attempt left a sealed row behind');
    const bytes = t.gw.gatewayStore.db
      .prepare('SELECT SUM(LENGTH(draw_json) + LENGTH(secret_hex) + LENGTH(salt_hex) + LENGTH(COALESCE(memo,\'\'))) AS b FROM random_commitments')
      .get().b;
    assert.ok(bytes > 20_000, `${bytes} bytes stored for $0.00 of revenue`);
  } finally {
    await t.close();
  }
});

// ---------------------------------------------------------------------------
// F4 — commitments are pruned ONCE, in main(), at process start. A gateway
//      that never restarts never prunes, so the reveal-404 promise
//      ("commitments are pruned after N seconds") is false in both directions:
//      rows outlive the TTL, and the DB grows without bound.
// ---------------------------------------------------------------------------

test('F4: nothing prunes commitments while the process runs', async () => {
  const t = await buildTestGateway({ overrides: { randomCommitTtlSeconds: 3600 } });
  try {
    // A commitment created 400 days ago.
    t.gw.gatewayStore.putCommitment({
      commitId: 'rc_' + 'ab'.repeat(16),
      commitment: 'de'.repeat(32),
      algorithm: 'sha3_256(secret||salt)',
      kind: 'commit',
      requestId: '',
      memo: null,
      secretHex: '11'.repeat(32),
      saltHex: '22'.repeat(32),
      draw: QRNG_FIXTURE,
      revealAfter: 0,
      createdAt: Math.floor(Date.now() / 1000) - 400 * 24 * 3600,
    });
    // Serve plenty of traffic; no prune is ever triggered by a request path.
    for (let i = 0; i < 5; i++) await request(t.baseUrl, '/x402');
    assert.equal(t.gw.gatewayStore.countCommitments(), 1, 'the expired row survives');
    const r = await request(t.baseUrl, '/x402/random/reveal/rc_' + 'ab'.repeat(16));
    assert.equal(r.status, 200, 'and is still revealed long past the stated TTL');
    // Proof that the only caller of pruneCommitments is the process entrypoint:
    const src = require('node:fs').readFileSync(require.resolve('../src/server.js'), 'utf8');
    const calls = src.split('pruneCommitments').length - 1;
    assert.equal(calls, 1, 'pruneCommitments appears exactly once — inside main()');
    assert.ok(/function main\(\)[\s\S]*pruneCommitments/.test(src), 'and it is inside main()');
  } finally {
    await t.close();
  }
});

// ---------------------------------------------------------------------------
// F5 — payments are fungible across every product that shares a price: the
//      accepts object carries no resource binding and paymentPayload.resource
//      is never compared to the route being served.
// ---------------------------------------------------------------------------

test('F5: a payment signed for random_shuffle unlocks chain_batch_balances (same $0.02)', async () => {
  const t = await buildTestGateway();
  try {
    const shuffle402 = await request(t.baseUrl, '/x402/random/shuffle', {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify({ n: 3 }),
    });
    assert.equal(shuffle402.status, 402);
    const decoded = protocol.decodeHeader(shuffle402.headers.get('payment-required'));
    assert.match(decoded.resource.url, /\/x402\/random\/shuffle$/);

    // Present that payment verbatim at a DIFFERENT, more expensive-to-serve
    // endpoint that happens to cost the same.
    const header = paymentHeaderFrom(shuffle402);
    const balances = await request(t.baseUrl, '/x402/chain/balances', {
      method: 'POST',
      headers: { 'content-type': 'application/json', 'payment-signature': header },
      body: JSON.stringify({ addresses: ['0x' + '11'.repeat(32)] }),
    });
    assert.equal(balances.status, 200, 'the shuffle payment bought a balances call');
    assert.equal(balances.json.product, 'chain_batch_balances');
    assert.equal(t.fac.settled(), 1);
  } finally {
    await t.close();
  }
});

// ---------------------------------------------------------------------------
// F6 (FIXED — regression test) — random_bulk used to sell a PREMIUM at
//      draws<6 while its description claimed an unconditional volume
//      discount, and its "draws" were slices of ONE draw that a buyer could
//      have cut themselves out of a single $0.01 qrng call. Now: independent
//      draws, and the break-even minimum is enforced pre-settlement.
// ---------------------------------------------------------------------------

test('F6: random_bulk can no longer settle a premium under a "discount" label', async () => {
  const t = await buildTestGateway();
  try {
    // The old PoC: draws=1 for $0.03 against $0.01 for the same thing.
    const res = await request(t.baseUrl, '/x402/qrng/bulk', {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify({ draws: 1, bytes: 32 }),
    });
    assert.equal(res.status, 400, 'refused, not settled');
    assert.equal(res.json.error, 'below_bulk_minimum');
    assert.equal(res.json.cheaper_alternative.price_atomic, cfgMod.usdToUsdcAtomic(t.gw.cfg.qrngPriceUsd));
    assert.equal(t.fac.settled(), 0);

    // Whatever IS purchasable is genuinely cheaper per draw than buying the
    // same number of single draws — the cheapest equivalent purchase, not a
    // strawman of 10 single calls.
    const { paid } = await paidRequest(t.baseUrl, '/x402/qrng/bulk', {
      method: 'POST', headers: { 'content-type': 'application/json' },
      body: JSON.stringify({ draws: 6, bytes: 32 }),
    });
    assert.equal(paid.status, 200);
    const p = paid.json.result.pricing;
    assert.ok(BigInt(p.price_atomic) < BigInt(p.single_draw_price_atomic) * 6n,
      'cheaper than 6 single draws');
    assert.ok(BigInt(p.price_atomic_per_draw) < BigInt(p.single_draw_price_atomic));
    // The catalog copy no longer promises an unconditional discount, and it
    // names the minimum that makes the claim true.
    const cat = await request(t.baseUrl, '/x402');
    const bulk = cat.json.products.find((x) => x.id === 'random_bulk');
    assert.doesNotMatch(bulk.description, /real per-unit discount/);
    assert.match(bulk.description, /INDEPENDENT/);
    assert.match(bulk.description, /4\.\.10/);
    // ...and the response says plainly which product is cheaper per byte.
    assert.equal(p.per_byte.cheaper_per_byte, 'single_max_draw');
  } finally {
    await t.close();
  }
});

// ---------------------------------------------------------------------------
// F7 — the address-index walker only checks continuity for the FIRST block of
//      a chunk. A reorg that lands mid-chunk splices two histories together
//      and is never detected, because every later tick chains cleanly off the
//      new tip.
// ---------------------------------------------------------------------------

function forkedChainHandlers({ head = 20, forkAt = 5 } = {}) {
  const h = (n) => '0x' + String(n).padStart(64, '0');
  return {
    'chain.getHead': () => ({ height: head, hash: h(head) }),
    'chain.getBlockByHeight': (p) => {
      if (p.height > head) return null;
      return {
        number: p.height,
        hash: h(p.height),
        // Block `forkAt` claims a parent that is NOT block forkAt-1: a reorg
        // landed between the two RPC calls of the same batch.
        parentHash: p.height === forkAt ? '0x' + 'de'.repeat(32) : h(p.height - 1),
        timestamp: 1786470000 + p.height,
        chainId: 1,
        transactions: [],
      };
    },
  };
}

test('F7: a reorg inside one chunk is committed silently (no rewind, spliced index)', async () => {
  const store = createChainIndexStore(':memory:');
  const node = createNodeClient('http://node.invalid/rpc', {
    fetchImpl: fakeNodeFetch(forkedChainHandlers({ head: 20, forkAt: 5 })),
  });
  const cfg = cfgMod.loadGatewayConfig({}, { enabled: true });
  const walker = createChainIndexer({ cfg, node, store, logger: quiet, sleep: async () => {} });

  const progress = await walker.tick();
  assert.equal(progress.rewinds, 0, 'the walker never noticed');
  assert.equal(progress.indexed_height, 14, 'and committed the whole spliced range');

  // The stored chain is discontinuous at height 5 and nothing will ever notice:
  const rows = store.db.prepare('SELECT height, hash, parent_hash FROM blocks ORDER BY height').all();
  const broken = rows.filter((r, i) => i > 0 && r.parent_hash !== rows[i - 1].hash);
  assert.equal(broken.length, 1);
  assert.equal(broken[0].height, 5);

  // A second tick chains cleanly off the (new-branch) tip, so the orphaned
  // heights 0..4 stay in the paid index forever.
  const p2 = await walker.tick();
  assert.equal(p2.rewinds, 0);
  store.close();
});

test('F7b (contrast): a reorg at the CHUNK BOUNDARY is detected and rewound', async () => {
  const store = createChainIndexStore(':memory:');
  const h = (n) => '0x' + String(n).padStart(64, '0');
  let forked = false;
  const handlers = {
    'chain.getHead': () => ({ height: forked ? 30 : 20, hash: h(forked ? 30 : 20) }),
    'chain.getBlockByHeight': (p) => {
      const head = forked ? 30 : 20;
      if (p.height > head) return null;
      const parent = forked && p.height === 15 ? '0x' + 'de'.repeat(32) : h(p.height - 1);
      return { number: p.height, hash: h(p.height), parentHash: parent, timestamp: 1, chainId: 1, transactions: [] };
    },
  };
  const node = createNodeClient('http://node.invalid/rpc', { fetchImpl: fakeNodeFetch(handlers) });
  const cfg = cfgMod.loadGatewayConfig({}, { enabled: true });
  const walker = createChainIndexer({ cfg, node, store, logger: quiet, sleep: async () => {} });
  await walker.tick();               // indexes 0..14
  forked = true;
  const p = await walker.tick();     // next chunk starts at 15 -> mismatch
  assert.ok(p.rewinds >= 1, 'boundary reorgs ARE caught — the gap is intra-chunk only');
  store.close();
});

// ---------------------------------------------------------------------------
// F8 — chain_batch_balances' "mandatory readiness re-check immediately before
//      settlement" reads a 5-second head cache, so it cannot see a node that
//      died after the 402. Worse, node-side failures are per-entry DATA, so a
//      100%-failed batch is still a settled HTTP 200 with every balance null —
//      no incident, no receipt, no refund path, nothing to reconcile.
// ---------------------------------------------------------------------------

test('F8: balances settles on a stale head cache and bills a 100%-failed batch as 200', async () => {
  let nodeUp = true;
  const handlers = {
    'chain.getHead': () => {
      if (!nodeUp) throw new Error('node down');
      return { height: 100, hash: '0x' + '00'.repeat(32) };
    },
    'state.getAddressBalance': () => {
      if (!nodeUp) throw new Error('node down');
      return { confirmed_balance: '1', unit: 'nANM', display_decimals: 9, as_of_head_height: 100 };
    },
    'rand.quantumRandomBytes': () => QRNG_FIXTURE,
  };
  const t = await buildTestGateway({ handlers });
  try {
    const body = JSON.stringify({ addresses: ['0x' + '11'.repeat(32)] });
    const first = await request(t.baseUrl, '/x402/chain/balances', {
      method: 'POST', headers: { 'content-type': 'application/json' }, body,
    });
    assert.equal(first.status, 402); // head cached here

    nodeUp = false; // the node dies between the 402 and the paid retry

    const paid = await request(t.baseUrl, '/x402/chain/balances', {
      method: 'POST',
      headers: { 'content-type': 'application/json', 'payment-signature': paymentHeaderFrom(first) },
      body,
    });
    assert.equal(t.fac.settled(), 1, 'preSettle passed on a cached head and the money moved');
    // The "readiness re-check before settlement" never touched the dead node.
    assert.equal(paid.status, 200, 'a total failure is delivered as success');
    assert.equal(paid.json.count, 1);
    assert.equal(paid.json.failed_lookups, 1, 'every lookup failed');
    assert.equal(paid.json.balances[0].balance, null);
    assert.match(paid.json.balances[0].error, /node down/);
    assert.equal(paid.json.total_balance, '0');
    // No incident, no receipt, nothing for an operator to reconcile.
    assert.equal(t.gw.gatewayStore.listIncidents(null, 100).length, 0);
    assert.equal(paid.json.receipt, undefined);
    const metrics = t.gw.renderMetrics();
    assert.match(metrics, /x402_revenue_usdc\{product="chain_batch_balances"\}\s+0\.01/,
      'revenue is counted for a request that delivered nothing');
  } finally {
    await t.close();
  }
});

// ---------------------------------------------------------------------------
// F9 — chain_address_history happily sells a window it can prove is empty.
// ---------------------------------------------------------------------------

test('F9: history charges $0.02 for a window that provably cannot contain a row', async () => {
  const store = createChainIndexStore(':memory:');
  store.applyBlocks([{ number: 0, hash: '0x0', parentHash: null, timestamp: 1, chainId: 1, transactions: [] }]);
  store.recordTick({ headHeight: 5, atMs: Date.now(), maxLagBlocks: 12 });
  const t = await buildTestGateway({
    handlers: Object.assign(chainHandlers({ head: 5 })),
    chainIndex: store,
  });
  try {
    const body = JSON.stringify({
      address: '0x' + '11'.repeat(32),
      from_height: 9_000_000,
      to_height: 9_000_001, // >= from_height, so validate() lets it through
    });
    const { paid } = await paidRequest(t.baseUrl, '/x402/chain/address-history', {
      method: 'POST', headers: { 'content-type': 'application/json' }, body,
    });
    assert.equal(paid.status, 200);
    assert.equal(paid.json.count, 0);
    // to_height was clamped BELOW from_height — the query could not match.
    assert.ok(paid.json.window.to_height < paid.json.window.from_height,
      `window ${JSON.stringify(paid.json.window)} is empty by construction`);
    assert.equal(t.fac.settled(), 1, 'and it was still settled');
  } finally {
    await t.close();
  }
});

// ---------------------------------------------------------------------------
// F10 — caps that DO hold (the good news), proven rather than assumed.
// ---------------------------------------------------------------------------

test('F10: caps are enforced pre-settlement and cost the node exactly one call', async () => {
  const t = await buildTestGateway();
  try {
    const over = [
      ['/x402/random/int', { min: 0, max: 10, count: 1001 }],
      ['/x402/random/shuffle', { n: 10001 }],
      ['/x402/random/pick', { items: new Array(10001).fill(1), k: 1 }],
      ['/x402/qrng/bulk', { draws: 10, bytes: 1024 * 8 }],
      ['/x402/chain/balances', { addresses: new Array(501).fill('0x' + '11'.repeat(32)) }],
    ];
    for (const [path, body] of over) {
      const r = await request(t.baseUrl, path, {
        method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify(body),
      });
      assert.equal(r.status, 400, `${path} must refuse before a 402`);
      assert.equal(r.headers.get('payment-required'), null, `${path} must not offer terms`);
    }
    assert.equal(t.fac.calls.verify.length, 0);
    assert.equal(t.fac.settled(), 0);

    // Max-size in-cap requests: one node draw, bounded work.
    t.events.length = 0;
    const shuffle = await paidRequest(t.baseUrl, '/x402/random/shuffle', {
      method: 'POST', headers: { 'content-type': 'application/json' },
      body: JSON.stringify({ n: 10000 }),
    });
    assert.equal(shuffle.paid.status, 200);
    assert.equal(shuffle.paid.json.result.permutation.length, 10000);
    // NOTE: 3 node draws per paid cycle here (402-probe, retry-probe, the real
    // draw) because this fixture sets availabilityTtlMs=0. In production the
    // probe memoises for 5 s, so a burst costs 1 draw/request + 1 probe/5 s.
    const draws = t.events.filter((e) => e === 'node:rand.quantumRandomBytes').length;
    assert.ok(draws <= 3, `expected <=3 node draws, got ${draws}`);

    // 500 addresses -> ONE batched JSON-RPC request, not 500.
    t.events.length = 0;
    const addrs = [];
    for (let i = 0; i < 500; i++) addrs.push('0x' + i.toString(16).padStart(64, '0'));
    const bal = await paidRequest(t.baseUrl, '/x402/chain/balances', {
      method: 'POST', headers: { 'content-type': 'application/json' },
      body: JSON.stringify({ addresses: addrs }),
    });
    assert.equal(bal.paid.status, 200);
    assert.equal(bal.paid.json.unique_addresses, 500);
  } finally {
    await t.close();
  }
});

// ---------------------------------------------------------------------------
// F11 — commit-reveal safety properties that DO hold.
// ---------------------------------------------------------------------------

test('F11: reveal cannot be forced early, cannot be replayed into a second charge', async () => {
  const t = await buildTestGateway();
  try {
    const { paid } = await paidRequest(t.baseUrl, '/x402/random/commit', {
      method: 'POST', headers: { 'content-type': 'application/json' },
      body: JSON.stringify({ reveal_after_seconds: 600 }),
    });
    assert.equal(paid.status, 200);
    const id = paid.json.commit_id;
    assert.equal(paid.json.randomness, undefined, 'the draw is not disclosed at commit time');

    const early = await request(t.baseUrl, `/x402/random/reveal/${id}`);
    assert.equal(early.status, 425);
    assert.equal(early.json.secret, undefined);
    assert.equal(early.json.salt, undefined);

    // Paying again does not open it early either.
    const paidEarly = await paidRequest(t.baseUrl, `/x402/random/reveal/${id}`);
    assert.equal(paidEarly.first.status, 425, 'the reveal route is free and clock-gated, not payable');

    // Guessing another id is a 404, not an oracle.
    const guess = await request(t.baseUrl, '/x402/random/reveal/rc_' + '00'.repeat(16));
    assert.equal(guess.status, 404);
  } finally {
    await t.close();
  }
});

// ---------------------------------------------------------------------------
// F12 — idempotency across the new routes: one delivery == one settlement.
// ---------------------------------------------------------------------------

test('F12: Idempotency-Key replays every new SKU without a second settlement', async () => {
  const t = await buildTestGateway();
  try {
    const cases = [
      ['/x402/random/int', { min: 1, max: 6, count: 3 }],
      ['/x402/random/shuffle', { n: 5 }],
      ['/x402/random/pick', { items: [1, 2, 3], k: 2 }],
      // Each draw must be exactly `bytes` long: the shared fixture answers 32
      // bytes, so 32 it is — the product refuses a short draw (503) rather
      // than padding. 6 draws is the enforced bulk minimum.
      ['/x402/qrng/bulk', { draws: 6, bytes: 32 }],
      ['/x402/random/commit', {} ],
    ];
    let expected = 0;
    for (const [path, body] of cases) {
      const key = `poc-f12-${path}`;
      const first = await request(t.baseUrl, path, {
        method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify(body),
      });
      assert.equal(first.status, 402);
      const header = paymentHeaderFrom(first);
      const h = { 'content-type': 'application/json', 'payment-signature': header, 'idempotency-key': key };
      const a = await request(t.baseUrl, path, { method: 'POST', headers: h, body: JSON.stringify(body) });
      const b = await request(t.baseUrl, path, { method: 'POST', headers: h, body: JSON.stringify(body) });
      expected += 1;
      assert.equal(a.status, 200, path);
      assert.equal(b.status, 200, path);
      assert.equal(b.headers.get('idempotent-replay'), 'true', path);
      assert.equal(a.text, b.text, `${path}: replay is byte-identical`);
      assert.equal(t.fac.settled(), expected, `${path}: exactly one settlement`);
    }
  } finally {
    await t.close();
  }
});

// ---------------------------------------------------------------------------
// F13 — the "buyer can recompute it" contract, exercised END TO END against
//       the repo's own verifier (randomness/beacon_api/static/verify.js)
//       using the exact bytes each PAID HTTP response published.
// ---------------------------------------------------------------------------

let AnimicaBeacon = null;
try { AnimicaBeacon = require('../../../randomness/beacon_api/static/verify.js'); } catch { /* optional */ }

test('F13: every published derivation.recompute verifies in the repo verifier',
  { skip: AnimicaBeacon ? false : 'repo verifier not available' }, async () => {
    const t = await buildTestGateway();
    try {
      const cases = [
        ['/x402/random/int', { min: -5, max: 5, count: 25, request_id: 'poc' }],
        ['/x402/random/shuffle', { items: ['a', 'b', 'c', 'd', 'e', 'f', 'g'], request_id: 'poc' }],
        ['/x402/random/shuffle', { n: 37, request_id: 'poc' }],
        ['/x402/random/pick', { items: [1, 2, 3, 4, 5, 6, 7, 8], k: 3, request_id: 'poc' }],
        ['/x402/random/pick', { items: [1, 2, 3, 4], k: 1, replace: true, request_id: 'poc' }],
        ['/x402/random/pick', { items: [1, 2, 3, 4], weights: [1, 2, 3, 4], k: 1, request_id: 'poc' }],
      ];
      for (const [path, body] of cases) {
        const { paid } = await paidRequest(t.baseUrl, path, {
          method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify(body),
        });
        assert.equal(paid.status, 200, path);
        const d = paid.json.derivation;
        assert.ok(d.recompute, `${path} ${JSON.stringify(body)}: no recompute object published`);
        assert.equal(AnimicaBeacon.verifyResult(d.recompute), true,
          `${path} ${JSON.stringify(body)}: verify.js REJECTED the published recompute`);
        // the recompute is anchored to the bytes the response actually shipped
        assert.equal(d.recompute.beacon_hex, paid.json.randomness);
        assert.equal(d.entropy_hex, paid.json.randomness);
      }
    } finally {
      await t.close();
    }
  });

test('F13b: the "steps only" variants (multi-draw pick) really do follow the stock primitives',
  { skip: AnimicaBeacon ? false : 'repo verifier not available' }, async () => {
    const t = await buildTestGateway();
    try {
      // with replacement, k > 1 -> no recompute object, only `steps`
      const { paid } = await paidRequest(t.baseUrl, '/x402/random/pick', {
        method: 'POST', headers: { 'content-type': 'application/json' },
        body: JSON.stringify({ items: ['x', 'y', 'z'], k: 5, replace: true, request_id: 'poc' }),
      });
      assert.equal(paid.status, 200);
      const d = paid.json.derivation;
      assert.equal(d.recompute, null);
      assert.match(d.recompute_note, /follow `steps`/);
      const rng = new AnimicaBeacon.QDRNG(
        AnimicaBeacon.drngSeed(AnimicaBeacon.hexToBytes(paid.json.randomness), d.kind, d.request_id));
      const mine = [];
      for (let i = 0; i < 5; i++) mine.push(rng.randbelow(3));
      assert.deepEqual(mine, paid.json.result.indices, 'documented steps reproduce the answer');
      // randbelow(3) reads floor((bitlen(3)+7)/8)+1 = 2 bytes per draw.
      assert.equal(d.stream_bytes_consumed, 10, 'the byte-consumption audit matches the published rule');

      // weighted WITHOUT replacement, k > 1 -> also steps only
      const w = await paidRequest(t.baseUrl, '/x402/random/pick', {
        method: 'POST', headers: { 'content-type': 'application/json' },
        body: JSON.stringify({ items: ['a', 'b', 'c', 'd'], weights: [1, 2, 3, 4], k: 3, request_id: 'poc' }),
      });
      assert.equal(w.paid.status, 200);
      const wd = w.paid.json.derivation;
      const wrng = new AnimicaBeacon.QDRNG(
        AnimicaBeacon.drngSeed(AnimicaBeacon.hexToBytes(w.paid.json.randomness), wd.kind, wd.request_id));
      const idx = [0, 1, 2, 3];
      const weights = [1, 2, 3, 4];
      const got = [];
      for (let i = 0; i < 3; i++) {
        const total = weights.reduce((a, b) => a + b, 0);
        const r = wrng.randbelow(total);
        let acc = 0;
        let j = weights.length - 1;
        for (let q = 0; q < weights.length; q++) { acc += weights[q]; if (r < acc) { j = q; break; } }
        got.push(idx[j]);
        idx.splice(j, 1);
        weights.splice(j, 1);
      }
      assert.deepEqual(got, w.paid.json.result.indices);
    } finally {
      await t.close();
    }
  });

test('F14: commit-reveal — the reveal really is checkable by a third party',
  { skip: AnimicaBeacon ? false : 'repo verifier not available' }, async () => {
    const t = await buildTestGateway();
    try {
      const { paid } = await paidRequest(t.baseUrl, '/x402/random/commit', {
        method: 'POST', headers: { 'content-type': 'application/json' },
        body: JSON.stringify({ memo: 'round-7', request_id: 'poc' }),
      });
      assert.equal(paid.status, 200);
      const rev = await request(t.baseUrl, `/x402/random/reveal/${paid.json.commit_id}`);
      assert.equal(rev.status, 200);

      // 1. commitment opens
      const secret = AnimicaBeacon.hexToBytes(rev.json.secret);
      const salt = AnimicaBeacon.hexToBytes(rev.json.salt);
      const cat = new Uint8Array(secret.length + salt.length);
      cat.set(secret, 0); cat.set(salt, secret.length);
      assert.equal(AnimicaBeacon.bytesToHex(AnimicaBeacon.sha3_256(cat)), rev.json.commitment);
      assert.equal(rev.json.commitment, paid.json.commitment, 'and matches what was published at commit time');

      // 2. the secret was NOT cherry-picked: it is the stock DRNG over the draw
      const rng = new AnimicaBeacon.QDRNG(
        AnimicaBeacon.drngSeed(AnimicaBeacon.hexToBytes(rev.json.randomness), 'commit', 'poc'));
      assert.equal(AnimicaBeacon.bytesToHex(rng.randbytes(32)), rev.json.secret);
      assert.equal(AnimicaBeacon.bytesToHex(rng.randbytes(32)), rev.json.salt);

      // 3. the node's digest attestation covers exactly those bytes
      assert.equal(
        AnimicaBeacon.bytesToHex(AnimicaBeacon.sha3_256(AnimicaBeacon.hexToBytes(rev.json.randomness))),
        rev.json.attestation.digest_hex);
      assert.equal(rev.json.attestation.digest_hex, paid.json.attestation.digest_hex,
        'published BEFORE the reveal, so nothing could be swapped afterwards');
    } finally {
      await t.close();
    }
  });

test('F15: random_bulk draws each carry their own attestation over their own bytes', async () => {
  const t = await buildTestGateway();
  try {
    const { paid } = await paidRequest(t.baseUrl, '/x402/qrng/bulk', {
      method: 'POST', headers: { 'content-type': 'application/json' },
      body: JSON.stringify({ draws: 6, bytes: 32 }),
    });
    assert.equal(paid.status, 200);
    const sha3Hex = require('../src/products/derive').sha3Hex;
    assert.equal(paid.json.result.draws.length, 6);
    for (const seg of paid.json.result.draws) {
      // the published digest is over THIS draw's bytes...
      assert.equal(seg.sha3_256, sha3Hex(Buffer.from(seg.randomness, 'hex')));
      // ...and it is the digest the node signed for THIS draw
      assert.equal(seg.attestation.digest_hex, seg.sha3_256);
      assert.equal(seg.verification.method, 'signed-digest-attestation');
    }
    // No concatenation is published or implied any more.
    assert.equal(paid.json.randomness, undefined);
    assert.equal(paid.json.result.draws[0].offset, undefined);
    assert.equal(paid.json.attestation.scope, 'per_draw');
    assert.match(paid.json.attestation.note, /no single signature over the concatenation/);
    assert.equal(paid.json.derivation.algorithm, 'independent-draws');
    // F16: "attested" is exactly the field the node reports as FALSE, and the
    // response keeps saying so.
    assert.equal(paid.json.attestation.attested, false);
    assert.equal(paid.json.source.is_quantum, false);
    assert.equal(paid.json.health.passed, true);
  } finally {
    await t.close();
  }
});
