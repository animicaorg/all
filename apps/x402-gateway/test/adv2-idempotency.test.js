'use strict';
/**
 * ADVERSARIAL REVIEW 2/3 — vector (8): idempotency abuse. The gateway keys the
 * replay store on (Idempotency-Key, payment_fingerprint) where the fingerprint
 * is sha256(canonicalJson(paymentPayload)). These PoCs probe:
 *
 *   - key collision across payers: two payers reusing the same key must NOT be
 *     able to read each other's paid results.
 *   - same key + a DIFFERENT payment: must not replay a stale result; runs for
 *     real (documents that a re-signed retry under one key is NOT deduped).
 *   - same key + same payment: the intended idempotent replay (one charge).
 *   - cross-RESOURCE replay: the store row omits the resource, so a single
 *     payment can be replayed at a DIFFERENT product that happens to share the
 *     same price (identical `accepts`). Demonstrated as a FINDING.
 */

const { test } = require('node:test');
const assert = require('node:assert');

const protocol = require('../src/protocol');
const { buildTestGateway, request } = require('./gateway-helpers');

/** Fetch a 402 and return the offered accepts[0] + resource url. */
async function offer(baseUrl, path) {
  const res = await request(baseUrl, path);
  assert.equal(res.status, 402);
  const required = protocol.decodeHeader(res.headers.get('payment-required'));
  return { accepted: required.accepts[0], resource: required.resource.url };
}

/** Build a full PAYMENT-SIGNATURE header, controlling payer `from` and `nonce`. */
function header({ accepted, resource, from = '0x' + 'ab'.repeat(20), nonce }) {
  return protocol.encodeHeader({
    x402Version: 2,
    resource,
    accepted,
    payload: {
      signature: '0x' + '11'.repeat(65),
      authorization: {
        from,
        to: accepted.payTo,
        value: accepted.amount,
        validAfter: '0',
        validBefore: '9999999999',
        nonce: nonce || '0x' + '00'.repeat(31) + '01',
      },
    },
  });
}

function pay(baseUrl, path, hdr, idemKey) {
  const headers = { 'payment-signature': hdr };
  if (idemKey) headers['idempotency-key'] = idemKey;
  return request(baseUrl, path, { headers });
}

const QRNG = '/x402/qrng/draw';

test('adv2/8 same key + SAME payment => idempotent replay, exactly one settlement', async () => {
  const t = await buildTestGateway();
  try {
    const o = await offer(t.baseUrl, QRNG);
    const hdr = header({ accepted: o.accepted, resource: o.resource, nonce: '0x' + 'a1'.repeat(32) });
    const first = await pay(t.baseUrl, QRNG, hdr, 'KEY-1');
    const second = await pay(t.baseUrl, QRNG, hdr, 'KEY-1');
    assert.equal(first.status, 200);
    assert.equal(second.status, 200);
    assert.equal(first.headers.get('idempotent-replay'), null, 'first is a live charge');
    assert.equal(second.headers.get('idempotent-replay'), 'true', 'second is served from the replay store');
    assert.equal(second.text, first.text, 'replay returns the identical stored body');
    assert.equal(t.fac.settled(), 1, 'exactly one settlement for the deduped pair');
  } finally {
    await t.close();
  }
});

test('adv2/8 same key + DIFFERENT payment (re-signed nonce) is NOT deduped — runs for real', async () => {
  const t = await buildTestGateway();
  try {
    const o = await offer(t.baseUrl, QRNG);
    const h1 = header({ accepted: o.accepted, resource: o.resource, nonce: '0x' + 'b1'.repeat(32) });
    const h2 = header({ accepted: o.accepted, resource: o.resource, nonce: '0x' + 'b2'.repeat(32) });
    const r1 = await pay(t.baseUrl, QRNG, h1, 'SHARED-KEY');
    const r2 = await pay(t.baseUrl, QRNG, h2, 'SHARED-KEY');
    assert.equal(r1.status, 200);
    assert.equal(r2.status, 200);
    assert.equal(r2.headers.get('idempotent-replay'), null,
      'a different payment under the same key is a cache MISS — no stale replay, but also no dedupe (charged twice)');
    assert.equal(t.fac.settled(), 2, 'two distinct payments => two settlements');
  } finally {
    await t.close();
  }
});

test('adv2/8 key collision across payers: payer B cannot read payer A\'s cached result', async () => {
  const t = await buildTestGateway();
  try {
    const o = await offer(t.baseUrl, QRNG);
    const hA = header({ accepted: o.accepted, resource: o.resource, from: '0x' + 'aa'.repeat(20), nonce: '0x' + 'c1'.repeat(32) });
    const hB = header({ accepted: o.accepted, resource: o.resource, from: '0x' + 'bb'.repeat(20), nonce: '0x' + 'c2'.repeat(32) });
    const a = await pay(t.baseUrl, QRNG, hA, 'COLLIDE');
    const b = await pay(t.baseUrl, QRNG, hB, 'COLLIDE'); // same Idempotency-Key, different payer/payment
    assert.equal(a.status, 200);
    assert.equal(b.status, 200);
    assert.equal(b.headers.get('idempotent-replay'), null,
      'distinct payment fingerprint namespaces the key — B is not handed A\'s stored row');
    assert.equal(t.fac.settled(), 2, 'each payer settles their own payment');
  } finally {
    await t.close();
  }
});

/**
 * FINDING (vector 8): the idempotency store's PRIMARY KEY is
 * (idem_key, payment_fingerprint) and paywall.replay() never checks that the
 * stored row's `resource` matches the endpoint being hit. When two products
 * share a price their `accepts` are byte-identical, so ONE signed payment
 * matches both. A payment first spent at product A, replayed with the same key
 * at product B, is served A's stored response at B's endpoint with no
 * re-verification and no second charge — an audit/isolation break for a spec
 * that requires "every payment tied to a product/resource id for per-product
 * audit". Not reachable in the DEFAULT config only because every product has a
 * distinct price; a single same-price config change arms it.
 */
test('adv2/8 cross-RESOURCE replay is refused: a payment is bound to the product it first paid for', async () => {
  // Force qrng ($0.01) down to echo's price ($0.005) so their accepts collide.
  const t = await buildTestGateway({ overrides: { qrngPriceUsd: '0.005' } });
  try {
    const ECHO = '/x402/paid/echo';
    const oEcho = await offer(t.baseUrl, ECHO);
    const oQrng = await offer(t.baseUrl, QRNG);
    assert.deepEqual(oQrng.accepted, oEcho.accepted, 'sanity: same price => identical requirements');

    // Pay ECHO with a key; its response is cached under (key, fingerprint).
    const hdr = header({ accepted: oEcho.accepted, resource: oEcho.resource, nonce: '0x' + 'd1'.repeat(32) });
    const echoPaid = await pay(t.baseUrl, ECHO, hdr, 'XRES');
    assert.equal(echoPaid.status, 200);
    assert.ok(echoPaid.json && echoPaid.json.echo, 'echo produced its own response');
    assert.equal(t.fac.settled(), 1);

    // Replay the SAME payment + key at the QRNG endpoint: refused, because the
    // idempotency identity includes the resource the payment first bought.
    const qrngReplay = await pay(t.baseUrl, QRNG, hdr, 'XRES');
    assert.equal(qrngReplay.status, 409, 'cross-product replay is a conflict, not a free draw');
    assert.equal(qrngReplay.json.error, 'idempotency_key_conflict');
    assert.equal(qrngReplay.json.resource, ECHO, 'the conflict names the resource the payment is bound to');
    assert.equal(qrngReplay.json.randomness, undefined, 'no qrng draw was served');
    assert.equal(t.fac.settled(), 1, 'and no second settlement occurred');
  } finally {
    await t.close();
  }
});
