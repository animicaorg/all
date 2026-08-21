'use strict';
/**
 * Outbound spending tests.
 *
 * This is the only code in the tree that moves money out, so almost every test
 * here asserts a REFUSAL. The one that matters most is the separation of
 * duties: the key that settles our incoming payments must never be the key
 * that spends, because a single confused purchase would otherwise drain the
 * float that all 38 products settle through.
 */

const { test } = require('node:test');
const assert = require('node:assert/strict');

const { createPayer, PayerError, atomicToUsd } = require('../src/payer');
const { createBuyProduct } = require('../src/products/buy');
const { createGatewayStore } = require('../src/store/gateway');
const { loadGatewayConfig } = require('../src/config');

const SPENDER_KEY = '22'.repeat(32);
const FACILITATOR_KEY = '11'.repeat(32);
const publicLookup = async () => [{ address: '93.184.216.34', family: 4 }];

function cfgWith(over = {}) {
  const c = loadGatewayConfig({ X402_MESH_BACKGROUND: '0' });
  return Object.assign(c, {
    execEnabled: true,
    execPrivateKey: SPENDER_KEY,
    execMaxPerCallUsd: 0.10,
    execMaxPerDayUsd: 1.00,
    execTimeoutMs: 5000,
  }, over);
}

const LANE = {
  scheme: 'exact', network: 'eip155:8453', maxAmountRequired: '12000',
  asset: '0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913',
  payTo: '0x00000000000000000000000000000000000ABCDE',
  extra: { name: 'USD Coin', version: '2', decimals: 6 },
};

function res(status, { body = '', headers = {} } = {}) {
  return {
    ok: status >= 200 && status < 300,
    status,
    headers: { get: (h) => headers[h.toLowerCase()] ?? null },
    text: async () => body,
  };
}

/** A merchant that answers 402 then delivers once paid. */
function merchant({ amount = '12000', deliver = true, settled = true, onPaid = () => {} } = {}) {
  const seen = [];
  const impl = async (url, init) => {
    const hdrs = (init && init.headers) || {};
    const paid = hdrs['payment-signature'] || hdrs['x-payment'];
    seen.push({ paid: Boolean(paid), headers: hdrs });
    if (!paid) {
      const offer = { x402Version: 2, accepts: [{ ...LANE, maxAmountRequired: amount }] };
      return res(402, { headers: { 'payment-required': Buffer.from(JSON.stringify(offer)).toString('base64') } });
    }
    onPaid(JSON.parse(Buffer.from(paid, 'base64').toString('utf8')));
    if (!deliver) return res(500, { body: 'broke', headers: settled ? { 'payment-response': 'x' } : {} });
    return res(200, { body: JSON.stringify({ ok: true }), headers: { 'content-type': 'application/json', ...(settled ? { 'payment-response': 'x' } : {}) } });
  };
  return { impl, seen };
}

// ---------------------------------------------------------------------------
// The separation of duties
// ---------------------------------------------------------------------------

test('a payer refuses to be the facilitator key', () => {
  const p = createPayer({ privateKeyHex: FACILITATOR_KEY });
  assert.throws(
    () => createPayer({ privateKeyHex: FACILITATOR_KEY, forbiddenAddresses: [p.address] }),
    (e) => {
      assert.equal(e.code, 'spender_is_facilitator');
      assert.match(e.message, /must never be the key that spends/);
      return true;
    },
  );
});

test('the buy product is disabled when the spender is the facilitator', async () => {
  const p = createPayer({ privateKeyHex: FACILITATOR_KEY });
  const prod = createBuyProduct({
    cfg: cfgWith({ execPrivateKey: FACILITATOR_KEY, facilitatorSpendGuardAddress: p.address }),
    gatewayStore: createGatewayStore(':memory:'),
    fetchImpl: async () => res(402),
  });
  assert.equal(prod.enabled, false, 'a product that would spend the settlement key must not be sellable');
  const avail = await prod.availability();
  assert.equal(avail.available, false);
  assert.match(avail.detail, /never be the key that spends/);
});

test('buying is off entirely with no dedicated key', async () => {
  const prod = createBuyProduct({
    cfg: cfgWith({ execPrivateKey: '' }),
    gatewayStore: createGatewayStore(':memory:'),
    fetchImpl: async () => res(402),
  });
  assert.equal(prod.enabled, false);
  assert.match((await prod.availability()).detail, /X402_EXEC_PRIVATE_KEY/);
});

// ---------------------------------------------------------------------------
// Ceilings, all enforced before signing
// ---------------------------------------------------------------------------

async function buy(prod, body) {
  return (await prod.handler({ params: prod.validate({ json: body }), requestId: 'r1' })).bodyObj;
}

function product(over = {}, m = merchant()) {
  return {
    prod: createBuyProduct({
      cfg: cfgWith(over.cfg), gatewayStore: over.store || createGatewayStore(':memory:'),
      fetchImpl: m.impl, lookup: publicLookup,
    }),
    m,
  };
}

test('a quote above the ceiling is refused before anything is signed', async () => {
  const m = merchant({ amount: '500000' });   // $0.50, over the $0.10 operator cap
  const { prod } = product({}, m);
  await assert.rejects(
    () => buy(prod, { resource: 'https://m.example/api' }),
    (e) => {
      assert.equal(e.body.error, 'over_ceiling');
      assert.match(e.body.detail, /Nothing was signed/);
      return true;
    },
  );
  assert.equal(m.seen.filter((s) => s.paid).length, 0, 'no paid request may go out');
});

test('a caller may tighten the operator cap but never loosen it', () => {
  const { prod } = product();
  assert.equal(prod.validate({ json: { resource: 'https://m.example/a', max_spend_usd: 0.01 } }).ceiling, 0.01);
  assert.equal(
    prod.validate({ json: { resource: 'https://m.example/a', max_spend_usd: 999 } }).ceiling, 0.10,
    'asking for a bigger allowance than the operator set must not grant one',
  );
});

test('the daily cap is persistent and blocks further buying', async () => {
  const store = createGatewayStore(':memory:');
  const day = new Date().toISOString().slice(0, 10);
  store.recordExecSpend({ day, resource: 'https://x/y', spent_usd: '1.00', outcome: 'paid', request_id: 'old', spent_at: 1 });
  const { prod, m } = product({ store });
  await assert.rejects(
    () => buy(prod, { resource: 'https://m.example/api' }),
    (e) => {
      assert.equal(e.body.error, 'daily_cap_reached');
      assert.match(e.body.detail, /resets at 00:00 UTC/);
      return true;
    },
  );
  assert.equal(m.seen.length, 0, 'the cap is checked before the merchant is even contacted for terms');
});

test('a purchase cannot overshoot the daily cap even under the per-call ceiling', async () => {
  const store = createGatewayStore(':memory:');
  const day = new Date().toISOString().slice(0, 10);
  store.recordExecSpend({ day, resource: 'https://x/y', spent_usd: '0.98', outcome: 'paid', request_id: 'old', spent_at: 1 });
  // $0.05 is under the $0.10 per-call cap, but only $0.02 of the day remains.
  const m = merchant({ amount: '50000' });
  const { prod } = product({ store }, m);
  await assert.rejects(() => buy(prod, { resource: 'https://m.example/api' }), (e) => {
    assert.equal(e.body.error, 'over_ceiling');
    return true;
  });
  assert.equal(m.seen.filter((s) => s.paid).length, 0);
});

// ---------------------------------------------------------------------------
// A real purchase
// ---------------------------------------------------------------------------

test('a purchase signs for exactly the quoted amount and records what it spent', async () => {
  let signedFor = null;
  const m = merchant({ onPaid: (p) => { signedFor = p; } });
  const store = createGatewayStore(':memory:');
  const { prod } = product({ store }, m);
  const d = await buy(prod, { resource: 'https://m.example/api', method: 'POST', body: { a: 1 } });

  assert.equal(d.purchased, true);
  assert.equal(d.spent_usd, 0.012);
  assert.equal(signedFor.payload.authorization.value, '12000', 'the signed value must equal the quote, not our own number');
  assert.equal(signedFor.payload.authorization.to, LANE.payTo);
  assert.ok(/^0x[0-9a-f]{40}$/i.test(signedFor.payload.authorization.from));
  assert.deepEqual(d.result.json, { ok: true });
  assert.equal(d.budget.spent_today_usd, 0.012, 'the ledger must reflect the spend immediately');
  assert.equal(d.budget.remaining_today_usd, 0.988);
});

test('an authorization is short-lived and uses a fresh nonce', async () => {
  const seenNonces = new Set();
  for (let i = 0; i < 3; i++) {
    let payload = null;
    const m = merchant({ onPaid: (p) => { payload = p; } });
    const { prod } = product({}, m);
    await buy(prod, { resource: 'https://m.example/api' });
    const a = payload.payload.authorization;
    assert.ok(!seenNonces.has(a.nonce), 'a nonce must never repeat');
    seenNonces.add(a.nonce);
    const ttl = Number(a.validBefore) - Math.floor(Date.now() / 1000);
    assert.ok(ttl > 0 && ttl <= 900, `authorization must expire soon, got ${ttl}s`);
  }
});

test('a dry run reads the terms and signs nothing', async () => {
  const m = merchant();
  const { prod } = product({}, m);
  const d = await buy(prod, { resource: 'https://m.example/api', dry_run: true });
  assert.equal(d.purchased, false);
  assert.equal(d.dry_run, true);
  assert.equal(d.quoted_usd, 0.012);
  assert.equal(d.spent_usd, 0);
  assert.equal(m.seen.filter((s) => s.paid).length, 0);
});

test('money that left is recorded even when delivery failed', async () => {
  // The merchant settled and then broke. Forgetting that spend would make
  // tomorrow's daily cap wrong in our favour, which is the wrong direction.
  const store = createGatewayStore(':memory:');
  const m = merchant({ deliver: false, settled: true });
  const { prod } = product({ store }, m);
  await assert.rejects(() => buy(prod, { resource: 'https://m.example/api' }), (e) => {
    assert.equal(e.body.error, 'paid_but_not_delivered');
    return true;
  });
  const day = new Date().toISOString().slice(0, 10);
  assert.equal(store.execSpentToday(day).total, 0.012, 'a settled-but-undelivered purchase still spent money');
});

test('a rejection with no settlement records no spend', async () => {
  const store = createGatewayStore(':memory:');
  const m = merchant({ deliver: false, settled: false });
  const { prod } = product({ store }, m);
  await assert.rejects(() => buy(prod, { resource: 'https://m.example/api' }));
  const day = new Date().toISOString().slice(0, 10);
  assert.equal(store.execSpentToday(day).total, 0, 'nothing settled, so nothing may be charged against the cap');
});

test('a resource resolving to a private address is never paid', async () => {
  let contacted = 0;
  const prod = createBuyProduct({
    cfg: cfgWith(), gatewayStore: createGatewayStore(':memory:'),
    fetchImpl: async () => { contacted++; return res(402); },
    lookup: async () => [{ address: '169.254.169.254', family: 4 }],
  });
  await assert.rejects(() => buy(prod, { resource: 'https://metadata.internal/api' }), (e) => {
    assert.equal(e.body.error, 'blocked_host');
    return true;
  });
  assert.equal(contacted, 0);
});

test('a resource that needs no payment returns its result and spends nothing', async () => {
  const store = createGatewayStore(':memory:');
  const prod = createBuyProduct({
    cfg: cfgWith(), gatewayStore: store, lookup: publicLookup,
    fetchImpl: async () => res(200, { body: '{"free":true}', headers: { 'content-type': 'application/json' } }),
  });
  const d = await buy(prod, { resource: 'https://m.example/free' });
  assert.equal(d.purchased, false);
  assert.equal(d.spent_usd, 0);
  assert.match(d.note, /without requiring payment/);
});

test('atomicToUsd honours declared decimals', () => {
  assert.equal(atomicToUsd('12000', { extra: { decimals: 6 } }), 0.012);
  assert.equal(atomicToUsd('1000000000000000000', { extra: { decimals: 18 } }), 1);
  assert.equal(atomicToUsd('7000', {}), 0.007);
});

test('a non-EVM-only merchant is refused rather than half-paid', async () => {
  const impl = async () => res(402, {
    headers: { 'payment-required': Buffer.from(JSON.stringify({ accepts: [{ network: 'solana:abc', amount: '1000' }] })).toString('base64') },
  });
  const prod = createBuyProduct({ cfg: cfgWith(), gatewayStore: createGatewayStore(':memory:'), fetchImpl: impl, lookup: publicLookup });
  await assert.rejects(() => buy(prod, { resource: 'https://m.example/api' }), (e) => {
    assert.equal(e.body.error, 'no_evm_lane');
    return true;
  });
});

test('exactly one payment header is sent, and it is the v2 one', async () => {
  // Sending `payment-signature` AND `x-payment` together made a live gateway
  // take its v1 path with a v2 payload and reject the purchase outright.
  let paidHeaders = null;
  const m = merchant({ onPaid: () => {} });
  const base = m.impl;
  const impl = async (url, init) => {
    const h = (init && init.headers) || {};
    if (h['payment-signature'] || h['x-payment']) paidHeaders = h;
    return base(url, init);
  };
  const prod = createBuyProduct({
    cfg: cfgWith(), gatewayStore: createGatewayStore(':memory:'), fetchImpl: impl, lookup: publicLookup,
  });
  await buy(prod, { resource: 'https://m.example/api' });
  assert.ok(paidHeaders['payment-signature'], 'the v2 header must be present');
  assert.equal(paidHeaders['x-payment'], undefined, 'the v1 header must NOT also be sent');
});
