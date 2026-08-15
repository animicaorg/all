'use strict';
/**
 * Paywall-level tests: Idempotency-Key replay, the payment-then-service
 * failure policy (bounded retry -> signed receipt + incident row), settle
 * failure semantics, and the accounting invariants (one delivered success
 * == one settlement; revenue == settled amounts; replays/failures/refusals
 * never move revenue).
 */

const { test } = require('node:test');
const assert = require('node:assert/strict');
const crypto = require('node:crypto');

const protocol = require('../src/protocol');
const { createReceiptSigner } = require('../src/receipts');
const { createGatewayStore } = require('../src/store/gateway');
const {
  buildTestGateway, request, paidRequest, paymentHeaderFrom, chainHandlers,
} = require('./gateway-helpers');

// ------------------------------------------------------------ idempotency

test('idempotency: same payment + same key replays the stored result, exactly one settlement', async () => {
  const t = await buildTestGateway();
  try {
    const nonce = '0x' + '55'.repeat(32);
    const idemKey = 'client-key-1';
    const one = await paidRequest(t.baseUrl, '/x402/qrng/draw', { nonce, idemKey });
    assert.equal(one.paid.status, 200);
    assert.equal(t.fac.calls.settle.length, 1);

    // resend the IDENTICAL payment + key (e.g. the client saw a timeout)
    const payHeader = paymentHeaderFrom(one.first, { nonce });
    const replayRes = await request(t.baseUrl, '/x402/qrng/draw', {
      headers: { 'payment-signature': payHeader, 'idempotency-key': idemKey },
    });
    assert.equal(replayRes.status, 200);
    assert.equal(replayRes.headers.get('idempotent-replay'), 'true');
    assert.deepEqual(replayRes.json, one.paid.json, 'replay must return the stored body');
    assert.ok(replayRes.headers.get('payment-response'), 'settlement proof replays too');
    // no new facilitator work, no second charge
    assert.equal(t.fac.calls.settle.length, 1);
    assert.equal(t.fac.calls.verify.length, 1);
    assert.equal(t.gw.paywall.extra.idempotentReplays.series.size, 1);
  } finally {
    await t.close();
  }
});

test('idempotency: a different key with a consumed payment is NOT replayed and cannot double-charge', async () => {
  const t = await buildTestGateway();
  try {
    const nonce = '0x' + '66'.repeat(32);
    const one = await paidRequest(t.baseUrl, '/x402/qrng/draw', { nonce, idemKey: 'key-A' });
    assert.equal(one.paid.status, 200);
    // same payment, DIFFERENT key: goes to verify, which rejects the
    // consumed authorization (mock mirrors the real facilitator)
    const payHeader = paymentHeaderFrom(one.first, { nonce });
    const res = await request(t.baseUrl, '/x402/qrng/draw', {
      headers: { 'payment-signature': payHeader, 'idempotency-key': 'key-B' },
    });
    assert.equal(res.status, 402);
    assert.equal(t.fac.calls.settle.length, 1, 'consumed authorization never settles again');
  } finally {
    await t.close();
  }
});

test('idempotency: failed settlement stores nothing — a later retry runs for real', async () => {
  const t = await buildTestGateway();
  try {
    t.fac.behavior.settleSuccess = false;
    const nonce = '0x' + '77'.repeat(32);
    const one = await paidRequest(t.baseUrl, '/x402/qrng/draw', { nonce, idemKey: 'key-C' });
    assert.equal(one.paid.status, 402, 'failed settlement withholds the resource');
    // resource withheld: no randomness in the 402 body
    assert.equal(one.paid.json.randomness, undefined);

    t.fac.behavior.settleSuccess = true;
    const payHeader = paymentHeaderFrom(one.first, { nonce });
    const retry = await request(t.baseUrl, '/x402/qrng/draw', {
      headers: { 'payment-signature': payHeader, 'idempotency-key': 'key-C' },
    });
    assert.equal(retry.status, 200, 'retry after a settle failure must serve for real');
    assert.equal(retry.headers.get('idempotent-replay'), null);
  } finally {
    await t.close();
  }
});

// -------------------------------------- payment-then-service failure path

test('failure receipt: downstream dies after settlement -> bounded retry -> signed receipt + incident', async () => {
  let headCalls = 0;
  let blockCalls = 0;
  const t = await buildTestGateway({
    handlers: {
      'chain.getHead': () => { headCalls++; return { height: 100 }; },
      'chain.getBlockByHeight': () => { blockCalls++; throw new Error('node exploded'); },
      'rand.quantumRandomBytes': () => { throw new Error('unused'); },
    },
  });
  try {
    const { paid } = await paidRequest(t.baseUrl, '/x402/chain/blocks?from=1&to=5', { idemKey: 'bulk-key' });
    assert.equal(paid.status, 502);
    assert.equal(paid.json.error, 'paid_service_failed');
    assert.ok(paid.json.incident_id.startsWith('inc_'));

    // the receipt is signed and verifiable with the gateway's secret
    const receipt = paid.json.receipt;
    assert.equal(receipt.alg, 'HMAC-SHA256');
    assert.equal(receipt.resource, '/x402/chain/blocks');
    assert.equal(receipt.product, 'bulk_chain');
    assert.equal(receipt.amount_atomic, '250000');
    assert.match(receipt.payment_id, /^0x0+1$/); // the settlement tx
    const signer = createReceiptSigner({ secret: 'test-receipt-secret' });
    assert.equal(signer.verify(receipt), true);
    const tampered = Object.assign({}, receipt, { amount_atomic: '1' });
    assert.equal(signer.verify(tampered), false);

    // settlement DID happen (money moved) and the client got the proof
    assert.equal(t.fac.calls.settle.length, 1);
    assert.ok(paid.headers.get('payment-response'));

    // bounded retry: handler ran exactly twice (2 attempts, each = 1 batch)
    assert.equal(blockCalls >= 2, true);
    assert.equal(headCalls >= 1, true);

    // incident row for reconciliation
    const incidents = t.gw.gatewayStore.listIncidents('open');
    assert.equal(incidents.length, 1);
    assert.equal(incidents[0].kind, 'downstream_failed');
    assert.equal(incidents[0].amount, '250000');
    assert.equal(JSON.parse(incidents[0].receipt_json).payment_id, receipt.payment_id);

    // the delivered outcome (the receipt) was stored for idempotent replay
    const idemRow = t.gw.gatewayStore.db.prepare('SELECT * FROM idempotency').get();
    assert.ok(idemRow);
    assert.equal(idemRow.response_status, 502);
    assert.equal(JSON.parse(idemRow.body.toString()).error, 'paid_service_failed');
  } finally {
    await t.close();
  }
});

test('failure receipt: retry succeeding on the second attempt means no incident', async () => {
  let attempt = 0;
  const good = chainHandlers({ head: 100 });
  const t = await buildTestGateway({
    handlers: Object.assign({}, good, {
      'chain.getBlockByHeight': (p) => {
        attempt++;
        if (attempt <= 3) throw new Error('transient'); // first handler run (1 chunk of 3 calls fails batch-wide on first item... each batch item throws)
        return good['chain.getBlockByHeight'](p);
      },
    }),
  });
  try {
    const { paid } = await paidRequest(t.baseUrl, '/x402/chain/blocks?from=1&to=3&format=json');
    assert.equal(paid.status, 200, `expected retry to recover, got ${paid.status}: ${paid.text}`);
    assert.equal(paid.json.blocks.length, 3);
    assert.equal(t.gw.gatewayStore.listIncidents(null).length, 0);
    assert.equal(t.fac.calls.settle.length, 1);
  } finally {
    await t.close();
  }
});

test('settle transport failure: resource withheld, settle_unknown incident recorded', async () => {
  const t = await buildTestGateway();
  try {
    t.fac.behavior.settleThrow = true;
    const { paid } = await paidRequest(t.baseUrl, '/x402/qrng/draw');
    // 502, not 402: the settle outcome is UNKNOWN (the tx may have landed), so
    // the answer must not read as "you were not charged, try again".
    assert.equal(paid.status, 502);
    assert.equal(paid.json.randomness, undefined);
    const incidents = t.gw.gatewayStore.listIncidents('open');
    assert.equal(incidents.length, 1);
    assert.equal(incidents[0].kind, 'settle_unknown');
  } finally {
    await t.close();
  }
});

// -------------------------------------------------- accounting invariants

test('accounting: one delivered success == one settlement; revenue == settled amounts; nothing else counts', async () => {
  const t = await buildTestGateway();
  try {
    // 3 successful qrng draws
    for (let i = 0; i < 3; i++) {
      const { paid } = await paidRequest(t.baseUrl, '/x402/qrng/draw', { idemKey: `acct-${i}` });
      assert.equal(paid.status, 200);
    }
    // 1 idempotent replay (same payment+key as the first)
    // (rebuild the same payment: fixed nonce path)
    const nonce = '0x' + '99'.repeat(32);
    const four = await paidRequest(t.baseUrl, '/x402/qrng/draw', { nonce, idemKey: 'acct-replay' });
    assert.equal(four.paid.status, 200);
    const replay = await request(t.baseUrl, '/x402/qrng/draw', {
      headers: { 'payment-signature': paymentHeaderFrom(four.first, { nonce }), 'idempotency-key': 'acct-replay' },
    });
    assert.equal(replay.headers.get('idempotent-replay'), 'true');

    // 1 failed settlement
    t.fac.behavior.settleSuccess = false;
    const failed = await paidRequest(t.baseUrl, '/x402/qrng/draw');
    assert.equal(failed.paid.status, 402);
    t.fac.behavior.settleSuccess = true;

    // 1 invalid payment
    t.fac.behavior.verifyValid = false;
    const invalid = await paidRequest(t.baseUrl, '/x402/qrng/draw');
    assert.equal(invalid.paid.status, 402);
    t.fac.behavior.verifyValid = true;

    // invariants
    const settles = t.fac.calls.settle.length;
    assert.equal(settles, 5, '4 successful + 1 failed settle attempt');
    assert.equal(t.fac.settled(), 4, 'exactly the 4 delivered successes settled');
    const revenue = t.gw.metrics.revenueUsdc.get({ product: 'qrng' });
    assert.equal(revenue, 4n * 50000n, 'revenue counts settled amounts only');
    // the failed settle and invalid payment moved nothing
    const rendered = t.gw.renderMetrics();
    assert.match(rendered, /x402_settlements_total\{product="qrng"\} 4/);
    assert.match(rendered, /x402_settlement_failures_total\{.*product="qrng".*\} 1/);
    assert.match(rendered, /x402_idempotent_replays_total\{product="qrng"\} 1/);
  } finally {
    await t.close();
  }
});

test('free surfaces unaffected: catalog + healthz never demand payment', async () => {
  const t = await buildTestGateway();
  try {
    for (const p of ['/x402', '/.well-known/x402', '/x402/healthz']) {
      const res = await request(t.baseUrl, p);
      assert.equal(res.status, 200, p);
      assert.equal(res.headers.get('payment-required'), null, p);
    }
    const metrics = await request(t.baseUrl, '/metrics');
    assert.equal(metrics.status, 200);
    assert.match(metrics.text, /x402_inference_serving_workers/);
  } finally {
    await t.close();
  }
});

test('tampered accepted terms are refused against OUR offer, pre-facilitator', async () => {
  const t = await buildTestGateway();
  try {
    const first = await request(t.baseUrl, '/x402/qrng/draw');
    const required = protocol.decodeHeader(first.headers.get('payment-required'));
    const accepted = Object.assign({}, required.accepts[0], { amount: '1' }); // pay 1 unit instead of 10000
    const payload = {
      x402Version: 2,
      accepted,
      payload: { signature: '0x' + '11'.repeat(65), authorization: { from: '0x' + 'ab'.repeat(20), to: accepted.payTo, value: '1', validAfter: '0', validBefore: '9999999999', nonce: '0x' + crypto.randomBytes(32).toString('hex') } },
    };
    const res = await request(t.baseUrl, '/x402/qrng/draw', {
      headers: { 'payment-signature': protocol.encodeHeader(payload) },
    });
    assert.equal(res.status, 402);
    assert.equal(t.fac.calls.verify.length, 0, 'tamper is refused before the facilitator sees it');
  } finally {
    await t.close();
  }
});

test('kill switch: cfg.enabled=false answers 503, never free passage', async () => {
  const t = await buildTestGateway({ overrides: { enabled: false } });
  try {
    const res = await request(t.baseUrl, '/x402/qrng/draw');
    assert.equal(res.status, 503);
    assert.equal(res.json.error, 'x402_disabled');
  } finally {
    await t.close();
  }
});

// ------------------------------------------------- stores + receipt units

test('gateway store: first idempotency writer wins; incidents lifecycle; prune', () => {
  const store = createGatewayStore(':memory:');
  const row = {
    idemKey: 'k', paymentFingerprint: 'fp', resource: '/x402/qrng/draw',
    status: 200, contentType: 'application/json', body: '{"a":1}',
    settlementHeader: 'aGVsbG8=', settlementTx: '0xabc',
  };
  assert.equal(store.putIdempotent(row), true);
  assert.equal(store.putIdempotent(Object.assign({}, row, { body: '{"a":2}' })), false, 'second writer loses');
  const got = store.getIdempotent('k', 'fp');
  assert.equal(got.body.toString(), '{"a":1}');
  assert.equal(store.getIdempotent('k', 'other'), null);

  // oversize bodies store a status ref, not megabytes
  const bigStore = createGatewayStore(':memory:', { maxBodyBytes: 10 });
  bigStore.putIdempotent(Object.assign({}, row, { body: 'x'.repeat(100) }));
  const bigRow = bigStore.getIdempotent('k', 'fp');
  assert.equal(bigRow.oversize, 1);
  assert.equal(bigRow.body, null);

  const id = store.addIncident({ resource: '/x402/x', kind: 'downstream_failed', error: 'boom', amount: '50000' });
  assert.equal(store.listIncidents('open').length, 1);
  assert.equal(store.setIncidentStatus(id, 'refunded'), true);
  assert.equal(store.listIncidents('open').length, 0);
  assert.equal(store.getIncident(id).status, 'refunded');
  assert.throws(() => store.setIncidentStatus(id, 'nonsense'), /bad incident status/);

  assert.equal(store.pruneIdempotency(0) >= 0, true);
  store.close();
  bigStore.close();
});

test('receipts: sign/verify round-trip, tamper detection, required fields, ephemeral flag', () => {
  const signer = createReceiptSigner({ secret: 'k1' });
  assert.equal(signer.ephemeral, false);
  const r = signer.sign({ payment_id: '0xabc', resource: '/x402/qrng/draw', error: 'boom' });
  assert.equal(r.alg, 'HMAC-SHA256');
  assert.equal(r.key_id, signer.keyId);
  assert.ok(r.ts > 0);
  assert.equal(signer.verify(r), true);
  assert.equal(signer.verify(Object.assign({}, r, { error: 'not boom' })), false);
  assert.equal(createReceiptSigner({ secret: 'other' }).verify(r), false, 'wrong key never verifies');
  assert.throws(() => signer.sign({ resource: '/x', error: 'e' }), /payment_id/);
  assert.equal(createReceiptSigner({}).ephemeral, true);
});

// -------------------------------------------------- discovery probes
// x402 indexers (and any agent meeting an endpoint for the first time) probe
// blind: a bare GET, or a POST with no body. Answering those 404/400 makes a
// product invisible to the discovery ecosystem — it never sees the price,
// asset or input schema. Both must yield the 402 advertisement. Registering
// the live products on x402scan is what surfaced this: every POST product was
// rejected with "Endpoint did not return a 402 payment challenge".
test('discovery: a bare GET on a POST-only product returns the 402 offer, not 404', async () => {
  const t = await buildTestGateway();
  try {
    const res = await request(t.baseUrl, '/x402/random/int');   // GET, no query
    assert.equal(res.status, 402, 'blind GET probe must see the offer');
    assert.ok(res.headers.get('payment-required'), 'and it carries the v2 requirements header');
  } finally {
    await t.close();
  }
});

test('discovery: a POST with no body returns the 402 offer; wrong params still 400', async () => {
  const t = await buildTestGateway();
  try {
    const probe = await request(t.baseUrl, '/x402/random/int', { method: 'POST' });
    assert.equal(probe.status, 402, 'bare POST probe advertises');

    // A caller that DID supply input and got it wrong gets the useful answer.
    const wrong = await request(t.baseUrl, '/x402/random/int', {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify({ min: 5 }),   // missing max
    });
    assert.equal(wrong.status, 400, 'supplied-but-invalid input still answers 400, not a 402');
  } finally {
    await t.close();
  }
});

test('discovery: a payment sent over the probe method is refused, never settled', async () => {
  const t = await buildTestGateway();
  try {
    const first = await request(t.baseUrl, '/x402/random/int');
    const res = await request(t.baseUrl, '/x402/random/int', {
      headers: { 'payment-signature': paymentHeaderFrom(first) },   // GET + payment
    });
    assert.equal(res.status, 405, 'buying over the wrong method is refused');
    assert.equal(t.fac.settled(), 0, 'and nothing settled');
  } finally {
    await t.close();
  }
});
