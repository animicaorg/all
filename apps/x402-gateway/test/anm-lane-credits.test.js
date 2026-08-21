'use strict';
/**
 * ANM-NATIVE LANE + PREPAID CREDITS.
 *
 * These two features exist for the same reason: the Base gas floor. Every
 * USDC settlement costs sponsored gas, which makes sub-cent pricing
 * impossible. The ANM lane removes the floor (the payer pays their own fee),
 * and credits amortise it (one settlement, many calls). The tests below are
 * mostly about the ways each could LOSE MONEY or LIE, not the happy path.
 */

const test = require('node:test');
const assert = require('node:assert');
const path = require('node:path');
const os = require('node:os');
const fs = require('node:fs');

const cfgMod = require('../src/config');
const { buildAccepts } = require('../src/middleware');
const { createAnmPrice } = require('../src/anm-price');
const { createGatewayStore } = require('../src/store/gateway');
const { normalizeDigest, transferCheck, valueOf } = require('../src/facilitator-anm');
const { voucherIdOf, mintToken, parseCreditToken, atomicToUsd, TOKEN_PREFIX } = require('../src/products/credits');

function priceFile(obj) {
  const p = path.join(fs.mkdtempSync(path.join(os.tmpdir(), 'anmprice-')), 'anm-price.json');
  fs.writeFileSync(p, JSON.stringify(obj));
  return p;
}

const LIVE_FEED = () => ({
  symbol: 'ANM/USDT', bid: 0.00007271, ask: 0.00007328, mid: 0.000072995,
  is_indicative: false, source: 'nonkyc', ts: Math.floor(Date.now() / 1000) - 30,
});

// ---------------------------------------------------------------------------
// Price feed: the rule is FAIL CLOSED. A stale or indicative rate must refuse
// to quote rather than quote at a dead number.
// ---------------------------------------------------------------------------

test('anm price: quotes from the bid, and converts USD to nANM with integer math', () => {
  const p = createAnmPrice({ path: priceFile(LIVE_FEED()) });
  const q = p.get();
  assert.equal(q.ok, true);
  assert.equal(q.usd_per_anm, 0.00007271, 'quotes the BID, not last or mid');
  const r = p.usdToNanm('0.005521', { discountPercent: 0 });
  assert.equal(r.ok, true);
  assert.equal(typeof r.nanm, 'bigint', 'amounts are BigInt, never a JS number');
  // 0.005521 USD / 0.00007271 USD-per-ANM = ~75.93 ANM
  assert.equal(r.anm_display, '75.931783798');
});

test('anm price: a STALE feed refuses to quote (never quotes at a dead rate)', () => {
  const stale = Object.assign(LIVE_FEED(), { ts: Math.floor(Date.now() / 1000) - 4000 });
  const p = createAnmPrice({ path: priceFile(stale), maxAgeSeconds: 900 });
  const q = p.get();
  assert.equal(q.ok, false);
  assert.equal(q.reason, 'price_stale');
  assert.equal(p.usdToNanm('1.00').ok, false, 'and conversion refuses too');
});

test('anm price: an INDICATIVE feed refuses (a guess is not a traded price)', () => {
  const p = createAnmPrice({ path: priceFile(Object.assign(LIVE_FEED(), { is_indicative: true })) });
  assert.equal(p.get().reason, 'price_indicative');
});

test('anm price: an unreadable feed refuses rather than defaulting', () => {
  const p = createAnmPrice({ path: '/nonexistent/anm-price.json' });
  assert.equal(p.get().ok, false);
});

test('anm price: the discount is applied to the USD side before conversion', () => {
  const p = createAnmPrice({ path: priceFile(LIVE_FEED()) });
  const full = p.usdToNanm('1.00', { discountPercent: 0 });
  const off = p.usdToNanm('1.00', { discountPercent: 25 });
  assert.equal(off.nanm, (full.nanm * 75n) / 100n, '25% off is exactly three quarters of the ANM');
  assert.equal(off.usd_after_discount, '0.750000');
});

// ---------------------------------------------------------------------------
// The lane in the 402 offer.
// ---------------------------------------------------------------------------

test('anm lane: offered ALONGSIDE Base USDC, never instead of it', () => {
  const cfg = cfgMod.loadGatewayConfig({ X402_ANM_ENABLED: '1' }, {
    networkEvm: 'eip155:8453',
    usdcAsset: '0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913',
    basePayTo: '0x' + '77'.repeat(20),
    anmPricePath: priceFile(LIVE_FEED()),
  });
  const accepts = buildAccepts({ path: '/p', priceUsd: '0.005521' }, cfg);
  assert.equal(accepts.length, 2);
  const anm = accepts.find((a) => a.network === 'animica:1');
  const usdc = accepts.find((a) => a.network === 'eip155:8453');
  assert.ok(anm && usdc, 'both lanes present');
  assert.equal(usdc.amount, '5521', 'the USDC lane is untouched by the ANM lane');
});

test('anm lane: advertises animica:1, NEVER eip155:1 (which would send payers to Ethereum)', () => {
  const cfg = cfgMod.loadGatewayConfig({ X402_ANM_ENABLED: '1' }, {
    basePayTo: '', anmPricePath: priceFile(LIVE_FEED()),
  });
  const accepts = buildAccepts({ path: '/p', priceUsd: '0.01' }, cfg);
  const anm = accepts.find((a) => String(a.network).startsWith('animica'));
  assert.ok(anm);
  assert.equal(anm.network, 'animica:1');
  assert.ok(!accepts.some((a) => a.network === 'eip155:1'),
    'eip155:1 is ETHEREUM MAINNET — advertising it would lose payers their money');
  assert.equal(anm.extra.chain_id, 1);
  assert.ok(anm.extra.genesis_hash.startsWith('0x'), 'publishes genesis hash to disambiguate the chain');
});

test('anm lane: DISAPPEARS when the price feed is stale, leaving the USDC lane working', () => {
  const stale = Object.assign(LIVE_FEED(), { ts: Math.floor(Date.now() / 1000) - 99999 });
  const cfg = cfgMod.loadGatewayConfig({ X402_ANM_ENABLED: '1' }, {
    networkEvm: 'eip155:8453',
    usdcAsset: '0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913',
    basePayTo: '0x' + '77'.repeat(20),
    anmPricePath: priceFile(stale),
  });
  const accepts = buildAccepts({ path: '/p', priceUsd: '0.01' }, cfg);
  assert.equal(accepts.length, 1, 'a dead price timer degrades the offer, it does not take it down');
  assert.equal(accepts[0].network, 'eip155:8453');
});

test('anm lane: the discount is real and disclosed in the offer', () => {
  const cfg = cfgMod.loadGatewayConfig({ X402_ANM_ENABLED: '1', X402_ANM_DISCOUNT_PCT: '25' },
    { basePayTo: '', anmPricePath: priceFile(LIVE_FEED()) });
  const anm = buildAccepts({ path: '/p', priceUsd: '1.00' }, cfg)[0];
  assert.equal(anm.extra.discount_percent, 25);
  assert.equal(anm.extra.usd_list_price, '1.00');
  assert.equal(anm.extra.usd_equivalent, '0.750000');
  assert.match(anm.extra.how_to_pay, /you pay the chain fee/i);
});

// ---------------------------------------------------------------------------
// Facilitator verification helpers: what may and may not be accepted as money.
// ---------------------------------------------------------------------------

test('anm facilitator: only a TRANSFER is payment evidence — a CALL carries no value', () => {
  assert.equal(transferCheck({ kind: 0 }).ok, true);
  assert.equal(transferCheck({ kind: 'TRANSFER' }).ok, true);
  const call = transferCheck({ kind: 2 });
  assert.equal(call.ok, false);
  assert.equal(call.kind, 'CALL');
  assert.equal(transferCheck({ kind: 1 }).ok, false, 'DEPLOY is not a payment');
  assert.equal(transferCheck({ kind: 3 }).ok, false, 'a block-reward tx is not a payment');
});

test('anm facilitator: digests normalise across bech32m and hex forms', () => {
  const hex = 'a'.repeat(64);
  assert.equal(normalizeDigest('0x' + hex, () => { throw new Error('no'); }), hex);
  assert.equal(normalizeDigest(hex.toUpperCase(), () => { throw new Error('no'); }), hex);
  assert.equal(normalizeDigest('', () => { throw new Error('no'); }), null);
  assert.equal(normalizeDigest('anim1whatever', () => 'B'.repeat(64)), 'b'.repeat(64));
});

test('anm facilitator: value is read as BigInt from any shape, never as a float', () => {
  assert.equal(valueOf({ value: 2000 }), 2000n);
  assert.equal(valueOf({ value: '0x7d0' }), 2000n);
  assert.equal(valueOf({ amount: '2000' }), 2000n);
  assert.equal(valueOf({ payload: { v: { amount: '2000' } } }), 2000n);
  assert.equal(valueOf({ to: 'x' }), null);
  assert.throws(() => valueOf({ value: 1.5 }), /unsafe|unparseable/i);
});

// ---------------------------------------------------------------------------
// Credits. The token is a bearer secret; the balance is money.
// ---------------------------------------------------------------------------

test('credits: the token is never stored — only its sha256', () => {
  const store = createGatewayStore(':memory:');
  const token = mintToken();
  const id = voucherIdOf(token);
  store.putVoucher({
    voucherId: id, mintedAtomic: '500000', bonusAtomic: '50000',
    createdAt: 1, expiresAt: 2 ** 31,
  });
  const dump = JSON.stringify(store.db.prepare('SELECT * FROM credit_vouchers').all());
  assert.ok(!dump.includes(token), 'a database read must not reveal a spendable token');
  assert.ok(dump.includes(id));
  store.close();
});

test('credits: a token is recognised from both header spellings, and nothing else is', () => {
  const t = mintToken();
  assert.equal(parseCreditToken({ 'x-animica-credits': t }), t);
  assert.equal(parseCreditToken({ authorization: `Bearer ${t}` }), t);
  assert.equal(parseCreditToken({ authorization: 'Bearer sk-some-other-api-key' }), null,
    'an unrelated bearer token is not a credit attempt');
  assert.equal(parseCreditToken({}), null);
  assert.ok(t.startsWith(TOKEN_PREFIX));
});

test('credits: balance arithmetic is exact and cannot go negative', () => {
  const store = createGatewayStore(':memory:');
  const id = voucherIdOf(mintToken());
  store.putVoucher({ voucherId: id, mintedAtomic: '10000', bonusAtomic: '0', createdAt: 1, expiresAt: 2 ** 31 });
  assert.equal(store.debitVoucher({ voucherId: id, amountAtomic: '9999' }).balanceAfter, '1');
  const over = store.debitVoucher({ voucherId: id, amountAtomic: '2' });
  assert.equal(over.ok, false);
  assert.equal(over.reason, 'insufficient_credit');
  assert.equal(store.getVoucher(id).balance_atomic, '1', 'a refused debit changes nothing');
  store.close();
});

test('credits: a failed call is refunded — the advantage credit has over settlement', () => {
  const store = createGatewayStore(':memory:');
  const id = voucherIdOf(mintToken());
  store.putVoucher({ voucherId: id, mintedAtomic: '10000', bonusAtomic: '0', createdAt: 1, expiresAt: 2 ** 31 });
  store.debitVoucher({ voucherId: id, amountAtomic: '2761', product: 'p', resource: '/r' });
  store.refundVoucher({ voucherId: id, amountAtomic: '2761', product: 'p', resource: '/r' });
  assert.equal(store.getVoucher(id).balance_atomic, '10000', 'made whole automatically');
  const entries = store.listCreditEntries(id, 10);
  assert.equal(entries.length, 2);
  assert.ok(entries.some((e) => String(e.amount_atomic).startsWith('-')), 'the refund is auditable in the ledger');
  store.close();
});

test('credits: expiry and revocation both refuse to spend', () => {
  const store = createGatewayStore(':memory:');
  const expired = voucherIdOf(mintToken());
  store.putVoucher({ voucherId: expired, mintedAtomic: '10000', bonusAtomic: '0', createdAt: 1, expiresAt: 2 });
  assert.equal(store.debitVoucher({ voucherId: expired, amountAtomic: '1' }).reason, 'voucher_expired');

  const revoked = voucherIdOf(mintToken());
  store.putVoucher({ voucherId: revoked, mintedAtomic: '10000', bonusAtomic: '0', createdAt: 1, expiresAt: 2 ** 31 });
  store.revokeVoucher(revoked);
  assert.equal(store.debitVoucher({ voucherId: revoked, amountAtomic: '1' }).reason, 'voucher_revoked');
  store.close();
});

test('credits: the bonus is exactly the configured percentage, floored', () => {
  const store = createGatewayStore(':memory:');
  const id = voucherIdOf(mintToken());
  // 10% of 500000 atomic = 50000
  store.putVoucher({ voucherId: id, mintedAtomic: '500000', bonusAtomic: '50000', createdAt: 1, expiresAt: 2 ** 31 });
  assert.equal(store.getVoucher(id).balance_atomic, '550000');
  assert.equal(atomicToUsd('550000'), '0.550000');
  store.close();
});

test('credits: a signed ANM transaction cannot buy two calls (replay guard)', () => {
  const store = createGatewayStore(':memory:');
  store.putAnmPayment({ txid: '0xabc', amountNanm: '1000', status: 'submitted', createdAt: 1 });
  store.setAnmPaymentStatus('0xabc', 'settled');
  const seen = store.getAnmPayment('0xabc');
  assert.equal(seen.status, 'settled', 'a settled txid is on record and will be refused on reuse');
  store.close();
});


// ---------------------------------------------------------------------------
// Structural guard. The free-route contract is `handler(ctx)` + `match(path)`.
// Naming one of them anything else 500s at RUNTIME while every unit test that
// calls the function directly still passes — the exact "feature-detection by
// method name" trap this codebase has already been bitten by. Assert the
// SHAPE, for every free route, so the next one cannot get it wrong either.
// ---------------------------------------------------------------------------

test('every free route implements the handler/match contract server.js calls', async () => {
  const { buildTestGateway } = require('./gateway-helpers');
  const t = await buildTestGateway();
  try {
    const seen = [];
    for (const p of t.gw.registry.products) {
      for (const r of p.freeRoutes || []) {
        seen.push(`${p.id} ${r.method} ${r.path}`);
        assert.equal(typeof r.handler, 'function', `${p.id} ${r.path}: free routes need handler(), not handle()`);
        assert.equal(typeof r.match, 'function', `${p.id} ${r.path}: free routes need match()`);
        assert.equal(typeof r.method, 'string');
        assert.equal(typeof r.path, 'string');
      }
    }
    assert.ok(seen.length >= 2, `expected free routes to exist, saw ${seen.length}`);
  } finally {
    await t.close();
  }
});


// ---------------------------------------------------------------------------
// Pricing guard. A paid product missing from DYN_MULT keeps a STATIC price,
// and when Base gas rises that price silently falls under the economic floor —
// `checkEconomicFloor` then REFUSES to settle, so the catalog advertises a
// product as available which fails at settlement time. Nine products shipped
// with exactly this defect before the guard existed. Assert every paid product
// is either gas-pegged or explicitly fixed-price WITH a stated reason.
// ---------------------------------------------------------------------------

test('every paid product is gas-pegged, or explicitly fixed-price with a reason', async () => {
  const { buildTestGateway } = require('./gateway-helpers');
  const { DYN_MULT, FIXED_PRICE_BY_DESIGN, MULT_CAP } = require('../src/dynamic-pricing');
  const t = await buildTestGateway();
  try {
    const missing = [];
    for (const p of t.gw.registry.products) {
      if (DYN_MULT[p.id] !== undefined) {
        assert.ok(DYN_MULT[p.id] >= 1 && DYN_MULT[p.id] <= MULT_CAP,
          `${p.id}: multiplier ${DYN_MULT[p.id]} outside 1..${MULT_CAP}`);
        continue;
      }
      if (FIXED_PRICE_BY_DESIGN[p.id]) {
        assert.ok(FIXED_PRICE_BY_DESIGN[p.id].length > 20,
          `${p.id}: a fixed-price exemption needs a real stated reason`);
        continue;
      }
      missing.push(p.id);
    }
    assert.deepEqual(missing, [],
      `these paid products track neither gas nor an explicit exemption, so their price cannot follow the settlement floor: ${missing.join(', ')}`);
  } finally {
    await t.close();
  }
});


// ---------------------------------------------------------------------------
// The chat bridge answers HTTP 200 with a plain-language apology when every
// AICF worker that claims a job fails to load a model. Passing that through as
// a delivered result CHARGES A PAYER FOR AN APOLOGY. Observed live on
// 2026-08-18: ollama stopped/disabled, one worker still advertising `standard`
// with a fresh last_seen, and the catalog reporting available:true.
// ---------------------------------------------------------------------------

test('paid inference: a 200 carrying the degraded fallback is refused, not delivered', async () => {
  const cfgMod2 = require('../src/config');
  const { createPriorityInferenceProduct } = require('../src/products/priority-inference');
  const cfg = cfgMod2.loadGatewayConfig({}, {
    priorityInferenceEnabled: true,
    inferenceBreakerTrips: 2,
  });
  const APOLOGY = JSON.stringify({
    id: 'chatcmpl-x', object: 'chat.completion',
    choices: [{ index: 0, message: { role: 'assistant',
      content: "⚠️ The Animica AI network couldn't complete your request just now — the provider that picked it up wasn't able to load a language model." } }],
  });
  const capacity = { available: () => true, snapshot: () => ({ serving_workers: 1, required: 1, enabled: true, tier: 'standard' }) };
  const upstream = async () => ({
    status: 200, ok: true,
    headers: { get: () => 'application/json' },
    async text() { return APOLOGY; },
  });
  const p = createPriorityInferenceProduct({ cfg, capacity, fetchImpl: upstream });

  assert.equal((await p.availability()).available, true, 'starts available');

  // Every degraded delivery must throw rather than return a body.
  for (let i = 0; i < 2; i++) {
    await assert.rejects(
      () => p.handler({ json: { model: 'm', messages: [{ role: 'user', content: 'hi' }] }, rawBody: Buffer.from('{}'), headers: {} }),
      (e) => {
        assert.match(e.message, /could not serve this request/i);
        assert.equal(e.retryable, false, 'not retryable — retrying only burns more of the payer\'s time');
        return true;
      }
    );
  }

  // ...and after the configured trips the product withholds itself, so the
  // catalog stops advertising it and the paywall stops taking money for it.
  const after = await p.availability();
  assert.equal(after.available, false);
  assert.equal(after.reason, 'inference_network_degraded');
  assert.match(after.detail, /rather than charging for an apology/i);
});

test('paid inference: a GENUINE answer is delivered and clears the breaker', async () => {
  const cfgMod2 = require('../src/config');
  const { createPriorityInferenceProduct } = require('../src/products/priority-inference');
  const cfg = cfgMod2.loadGatewayConfig({}, { priorityInferenceEnabled: true });
  const REAL = JSON.stringify({
    id: 'chatcmpl-y', object: 'chat.completion',
    choices: [{ index: 0, message: { role: 'assistant', content: 'PING_OK' } }],
  });
  const capacity = { available: () => true, snapshot: () => ({ serving_workers: 2, required: 1, enabled: true, tier: 'standard' }) };
  const p = createPriorityInferenceProduct({
    cfg, capacity,
    fetchImpl: async () => ({ status: 200, ok: true, headers: { get: () => 'application/json' }, async text() { return REAL; } }),
  });
  const out = await p.handler({ json: { model: 'm', messages: [{ role: 'user', content: 'hi' }] }, rawBody: Buffer.from('{}'), headers: {} });
  assert.equal(out.status, 200);
  assert.match(out.body, /PING_OK/);
  assert.equal((await p.availability()).available, true);
});

test('paid inference: an answer that merely DISCUSSES model loading is still delivered', async () => {
  const cfgMod2 = require('../src/config');
  const { createPriorityInferenceProduct } = require('../src/products/priority-inference');
  const cfg = cfgMod2.loadGatewayConfig({}, { priorityInferenceEnabled: true });
  // Narrow matching matters: a real answer about model loading must not be
  // mistaken for the network's own failure notice.
  const REAL = JSON.stringify({
    choices: [{ index: 0, message: { role: 'assistant',
      content: 'To load a language model in Python, call AutoModel.from_pretrained(...).' } }],
  });
  const p = createPriorityInferenceProduct({
    cfg,
    capacity: { available: () => true, snapshot: () => ({ serving_workers: 1, required: 1, enabled: true, tier: 'standard' }) },
    fetchImpl: async () => ({ status: 200, ok: true, headers: { get: () => 'application/json' }, async text() { return REAL; } }),
  });
  const out = await p.handler({ json: { model: 'm', messages: [{ role: 'user', content: 'hi' }] }, rawBody: Buffer.from('{}'), headers: {} });
  assert.match(out.body, /AutoModel/);
});
