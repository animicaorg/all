'use strict';
/**
 * ADVERSARIAL REVIEW 2/3 — vector (7): malformed x402 payloads. Fuzz the
 * base64 / JSON parse paths, oversized bodies, prototype-pollution keys, and
 * pathologically-nested structures on BOTH the gateway header path
 * (PAYMENT-SIGNATURE / X-PAYMENT) and the facilitator HTTP body path.
 *
 * The gateway's own stated invariant (src/protocol.js, decodeClientHeader):
 * "garbage base64 / garbage JSON is a malformed payment ... it must produce a
 * structured 402 re-offer, never an unhandled exception (which the HTTP layer
 * would answer 500)." These PoCs test that promise. One case breaks it and is
 * reported in the review summary (deep-nested `accepted` => 500).
 */

const { test } = require('node:test');
const assert = require('node:assert');
const http = require('node:http');

const protocol = require('../src/protocol');
const { buildTestGateway, request } = require('./gateway-helpers');
const { scene } = require('./evm-helpers');
const { createEvmFacilitatorServer } = require('../src/facilitator-evm/server');

const QRNG = '/x402/qrng/draw';
const b64 = (obj) => Buffer.from(JSON.stringify(obj), 'utf8').toString('base64');

/* ======================= gateway header parse fuzzing ==================== */

test('adv2/7 gateway: garbage base64 in PAYMENT-SIGNATURE => clean 402, no 500', async () => {
  const t = await buildTestGateway();
  try {
    for (const bad of ['!!!!not base64!!!!', 'SGVsbG8=', /* "Hello" not JSON */ btoa('not json at all'), '{}=']) {
      const res = await request(t.baseUrl, QRNG, { headers: { 'payment-signature': bad } });
      assert.equal(res.status, 402, `bad header ${JSON.stringify(bad)} must re-offer 402, got ${res.status}`);
    }
  } finally {
    await t.close();
  }
});

test('adv2/7 gateway: valid base64 of a JSON ARRAY / scalar => 402 (must decode to object)', async () => {
  const t = await buildTestGateway();
  try {
    for (const val of [[1, 2, 3], 42, 'a string', null]) {
      const res = await request(t.baseUrl, QRNG, { headers: { 'payment-signature': b64(val) } });
      assert.equal(res.status, 402, `payload ${JSON.stringify(val)} must be rejected as 402`);
    }
  } finally {
    await t.close();
  }
});

test('adv2/7 gateway: wrong x402Version / missing accepted => 402', async () => {
  const t = await buildTestGateway();
  try {
    const v1 = b64({ x402Version: 1, accepted: {}, payload: {} });
    const noAccepted = b64({ x402Version: 2, payload: {} });
    const acceptedArray = b64({ x402Version: 2, accepted: [1, 2], payload: {} });
    assert.equal((await request(t.baseUrl, QRNG, { headers: { 'payment-signature': v1 } })).status, 402);
    assert.equal((await request(t.baseUrl, QRNG, { headers: { 'payment-signature': noAccepted } })).status, 402);
    assert.equal((await request(t.baseUrl, QRNG, { headers: { 'payment-signature': acceptedArray } })).status, 402);
  } finally {
    await t.close();
  }
});

test('adv2/7 gateway: prototype-pollution keys in the payload do NOT pollute Object.prototype', async () => {
  const t = await buildTestGateway();
  try {
    // JSON.parse creates an OWN "__proto__" property (no setter invoked), so
    // reading it in canonicalJson is inert; assert the global prototype stays
    // clean and the request is handled (402, not 500/crash).
    const poison = b64({
      x402Version: 2,
      accepted: { __proto__: { polluted: 'yes' }, constructor: { prototype: { polluted: 'yes' } }, scheme: 'exact' },
      payload: { authorization: {} },
    });
    const res = await request(t.baseUrl, QRNG, { headers: { 'payment-signature': poison } });
    assert.equal(res.status, 402, 'poisoned payload should just fail to match => 402');
    assert.equal({}.polluted, undefined, 'Object.prototype must not be polluted');
    assert.equal(Object.prototype.polluted, undefined);
  } finally {
    await t.close();
  }
});

test('adv2/7 gateway: oversized PAYMENT-SIGNATURE header (> maxHeaderSize) is refused (431/connection-drop), never served', async () => {
  const t = await buildTestGateway();
  try {
    const huge = 'A'.repeat(http.maxHeaderSize + 5000); // exceeds Node's header cap
    let status = null;
    let threw = false;
    try {
      const res = await request(t.baseUrl, QRNG, { headers: { 'payment-signature': huge } });
      status = res.status;
    } catch (e) {
      threw = true; // Node may reset the socket instead of answering 431
    }
    assert.ok(threw || status === 431 || status === 400,
      `oversized header must be refused (got status=${status}, threw=${threw})`);
    assert.equal(t.fac.settled(), 0);
  } finally {
    await t.close();
  }
});

/**
 * FINDING (vector 7): a single small request whose `accepted` value nests a
 * deeply-recursive array drives protocol.canonicalJson (called by
 * requirementsEqual at match time, and again for the idempotency fingerprint)
 * into a stack overflow. handleProduct does not wrap the match step, so the
 * RangeError escapes to server.js's top-level catch and the client receives a
 * 500 internal_error — exactly the "unhandled exception / HTTP 500" that
 * protocol.js's decodeClientHeader comment promises malformed payments will
 * NEVER produce. The whole payload fits well under Node's 16 KB header cap, so
 * it is remotely reachable and cheap to send.
 */
test('adv2/7 gateway: deeply nested `accepted` is rejected as malformed (402), never a 500', async () => {
  const t = await buildTestGateway();
  try {
    const depth = 4000; // empirically past the ~3500 canonicalJson overflow point
    let nested = 0;
    for (let i = 0; i < depth; i++) nested = [nested];
    const header = b64({
      x402Version: 2,
      accepted: { scheme: 'exact', deep: nested },
      payload: { signature: '0x' + '11'.repeat(65), authorization: {} },
    });
    assert.ok(header.length < http.maxHeaderSize,
      `PoC header is ${header.length} bytes — under the ${http.maxHeaderSize}-byte cap, so remotely reachable`);
    const res = await request(t.baseUrl, QRNG, { headers: { 'payment-signature': header } });
    // FIXED: depth is capped before canonicalisation, so a hostile payload is
    // a normal malformed-payment 402 re-offer rather than an unhandled
    // RangeError surfacing as 500 (which was a cheap remote DoS).
    assert.equal(res.status, 402, 'malformed nesting yields a clean 402 re-offer');
    assert.equal(t.fac.settled(), 0);
  } finally {
    await t.close();
  }
});

/* ======================= facilitator HTTP body fuzzing =================== */

async function withFacilitatorServer(fn) {
  const sc = scene();
  const server = createEvmFacilitatorServer(sc.facilitator);
  await new Promise((r) => server.listen(0, '127.0.0.1', r));
  const base = `http://127.0.0.1:${server.address().port}`;
  try {
    return await fn({ sc, base });
  } finally {
    await new Promise((r) => server.close(r));
  }
}

test('adv2/7 facilitator: oversized /verify body => 413, garbage JSON => 400', async () => {
  await withFacilitatorServer(async ({ base }) => {
    const oversized = 'x'.repeat(65 * 1024 + 10); // > MAX_BODY_BYTES (64K)
    const r413 = await fetch(base + '/verify', { method: 'POST', headers: { 'content-type': 'application/json' }, body: oversized });
    assert.equal(r413.status, 413, 'oversized body must 413');
    const r400 = await fetch(base + '/verify', { method: 'POST', headers: { 'content-type': 'application/json' }, body: '{not json' });
    assert.equal(r400.status, 400, 'unparseable JSON must 400');
  });
});

test('adv2/7 facilitator: null / array / prototype-pollution bodies => isValid:false, no crash, no pollution', async () => {
  await withFacilitatorServer(async ({ base }) => {
    for (const body of ['null', '[]', JSON.stringify({ x402Version: 2, __proto__: { polluted: 1 }, paymentPayload: { __proto__: { p: 1 } }, paymentRequirements: {} })]) {
      const res = await fetch(base + '/verify', { method: 'POST', headers: { 'content-type': 'application/json' }, body });
      assert.equal(res.status, 200, 'facilitator answers the protocol envelope with 200 + isValid:false');
      const j = await res.json();
      assert.equal(j.isValid, false, `malformed body ${body} must be isValid:false`);
    }
    assert.equal({}.polluted, undefined, 'Object.prototype must be clean');
    assert.equal({}.p, undefined);
  });
});

test('adv2/7 facilitator: verify() never throws on structurally-broken payloads (returns typed reason)', async () => {
  const sc = scene();
  const broken = [
    { x402Version: 2 }, // no payload / requirements
    { x402Version: 2, paymentPayload: {}, paymentRequirements: { scheme: 'exact', network: sc.cfg.caip2, amount: '10000', asset: sc.cfg.asset, payTo: sc.payTo } },
    { x402Version: 2, paymentPayload: { payload: { signature: '0xbad', authorization: {} } }, paymentRequirements: { scheme: 'exact', network: sc.cfg.caip2, amount: '10000', asset: sc.cfg.asset, payTo: sc.payTo } },
  ];
  for (const b of broken) {
    const v = await sc.facilitator.verify(b);
    assert.equal(v.isValid, false);
    assert.ok(typeof v.invalidReason === 'string' && v.invalidReason.length > 0, 'every rejection carries a typed reason');
  }
});
