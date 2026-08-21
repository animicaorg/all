'use strict';
/**
 * SCAN DIRECTORY, ADOPTION BOUNTY, BLOCK-REWARD LEASE.
 *
 * These three move real value outward (a listing confers credibility, a
 * bounty pays ANM, a lease sells a share of a stream), so the tests below are
 * about the ways each could give away something it should not.
 */

const test = require('node:test');
const assert = require('node:assert');
const path = require('node:path');
const os = require('node:os');
const fs = require('node:fs');

const cfgMod = require('../src/config');
const { createGatewayStore } = require('../src/store/gateway');
const { createScanService } = require('../src/scan');
const { createAnmPrice } = require('../src/anm-price');
const { createLeaseProduct } = require('../src/products/lease');

function priceFile(bid = 0.00007271) {
  const p = path.join(fs.mkdtempSync(path.join(os.tmpdir(), 'anmp-')), 'p.json');
  fs.writeFileSync(p, JSON.stringify({
    symbol: 'ANM/USDT', bid, ask: bid * 1.01, mid: bid, is_indicative: false,
    source: 'nonkyc', ts: Math.floor(Date.now() / 1000) - 10,
  }));
  return p;
}

function cfgFor(over = {}) {
  return cfgMod.loadGatewayConfig({ X402_ANM_ENABLED: '1' }, Object.assign({
    anmPricePath: priceFile(),
    bountyEnabled: true,
    bountyAmountUsd: '1.00',
    bountyTreasuryReserveAnm: '10000',
    scanEnabled: true,
    leaseEnabled: true,
    leaseTreasuryAnmPerBlock: '75',
    leaseMaxSoldPct: 50,
    leasePriceUsd: '0.50',
    leaseDiscountPercent: 10,
  }, over));
}

/** Minimal res double. */
function fakeRes() {
  return {
    statusCode: null, headers: null, body: null, ended: false,
    writeHead(s, h) { this.statusCode = s; this.headers = h; },
    end(b) { this.body = b; this.ended = true; },
    get json() { try { return JSON.parse(this.body); } catch { return null; } },
  };
}

function fakeReq(method, body) {
  const chunks = body === undefined ? [] : [Buffer.from(JSON.stringify(body))];
  return Object.assign({
    method,
    headers: { 'content-type': 'application/json', 'x-forwarded-for': '203.0.113.9' },
    socket: { remoteAddress: '203.0.113.9' },
    async *[Symbol.asyncIterator]() { for (const c of chunks) yield c; },
  });
}

/** A node double: treasury balance in nANM. */
function fakeNode(balanceNanm, height = 76800) {
  return {
    async call(method) {
      if (method === 'state.getAddressBalance') return { confirmed_balance: String(balanceNanm) };
      if (method === 'chain.getHead') return { height, hash: '0xabc' };
      throw new Error(`unexpected ${method}`);
    },
  };
}

// ---------------------------------------------------------------------------
// The prober: what may be listed at all.
// ---------------------------------------------------------------------------

function scanWith(fetchImpl, over = {}) {
  const cfg = cfgFor(over);
  const store = createGatewayStore(':memory:');
  const scan = createScanService({ cfg, gatewayStore: store, node: fakeNode(200000n * 1000000000n), fetchImpl });
  return { cfg, store, scan };
}

function res402(accepts, status = 402) {
  return {
    status,
    headers: { get: () => null },
    async text() { return JSON.stringify({ accepts }); },
  };
}

test('scan: a 402 WITHOUT an animica lane is refused, with an actionable reason', async () => {
  const { scan, store } = scanWith(async () => res402([{ network: 'eip155:8453', payTo: '0xabc', maxAmountRequired: '5000' }]));
  const r = await scan.probe('https://example.com/paid');
  assert.equal(r.ok, false);
  assert.equal(r.reason, 'no_anm_lane');
  assert.match(r.detail, /animica/i);
  store.close();
});

test('scan: a real animica lane is accepted and its terms recorded', async () => {
  const { scan, store } = scanWith(async () => res402([
    { network: 'eip155:8453', payTo: '0xabc', maxAmountRequired: '5000' },
    { network: 'animica:1', payTo: 'anim1someoneelse', maxAmountRequired: '41259799202', asset: 'ANM' },
  ]));
  const r = await scan.probe('https://example.com/paid');
  assert.equal(r.ok, true);
  assert.equal(r.network, 'animica:1');
  assert.equal(r.payTo, 'anim1someoneelse');
  assert.equal(r.priceNanm, '41259799202');
  store.close();
});

test('scan: anything that is not a 402 is refused (a catalog page is not a paid route)', async () => {
  const { scan, store } = scanWith(async () => ({ status: 200, headers: { get: () => null }, async text() { return '{}'; } }));
  const r = await scan.probe('https://example.com/');
  assert.equal(r.ok, false);
  assert.equal(r.reason, 'no_402');
  store.close();
});

test('scan: refuses to probe private space — the directory is not an internal scanner', async () => {
  const { scan, store } = scanWith(async () => { throw new Error('should never be fetched'); });
  for (const u of ['http://169.254.169.254/', 'http://127.0.0.1:8545/', 'http://10.0.0.1/']) {
    const r = await scan.probe(u);
    assert.equal(r.ok, false, u);
    assert.equal(r.status, 'rejected', u);
    assert.equal(r.reason, 'blocked_address', u);
  }
  store.close();
});

test('scan: a URL we refuse to fetch is NOT stored (it cannot be used to fill the directory)', async () => {
  const { scan, store } = scanWith(async () => { throw new Error('nope'); });
  const res = fakeRes();
  await scan.handle(fakeReq('POST', { url: 'http://169.254.169.254/' }), res, new URL('http://x/x402/scan/register'), '/x402/scan/register', null);
  assert.equal(res.statusCode, 400);
  assert.equal(store.countScanServices().total, 0);
  store.close();
});

// ---------------------------------------------------------------------------
// Bounty: it must never promise what the treasury cannot pay.
// ---------------------------------------------------------------------------

test('bounty: refuses when the treasury cannot cover the claim on top of what is reserved', async () => {
  const cfg = cfgFor();
  const store = createGatewayStore(':memory:');
  // 10,001 ANM: above the 10,000 reserve by only 1 ANM, far under a $1 bounty
  // (~13,750 ANM at the test rate).
  const scan = createScanService({ cfg, gatewayStore: store, node: fakeNode(10001n * 1000000000n), fetchImpl: async () => res402([{ network: 'animica:1', payTo: 'anim1other' }]) });
  const anmPrice = createAnmPrice({ path: cfg.anmPricePath });
  const res = fakeRes();
  await scan.handle(
    fakeReq('POST', { url: 'https://example.com/paid', payout_address: 'anim1qqqqqqqqqqqqqqqqqqqqqqqq' }),
    res, new URL('http://x/x402/bounty/claim'), '/x402/bounty/claim', anmPrice
  );
  assert.equal(res.statusCode, 503);
  assert.equal(res.json.error, 'budget_exhausted');
  assert.equal(store.listBountyClaims({}).length, 0, 'nothing is recorded when it cannot be funded');
  store.close();
});

test('bounty: a funded claim against a real ANM lane is verified and reserved', async () => {
  const cfg = cfgFor({ bountyMode: 'open' });
  const store = createGatewayStore(':memory:');
  const scan = createScanService({
    cfg, gatewayStore: store, node: fakeNode(500000n * 1000000000n),
    fetchImpl: async () => res402([{ network: 'animica:1', payTo: 'anim1someoneelse', maxAmountRequired: '1000' }]),
  });
  const anmPrice = createAnmPrice({ path: cfg.anmPricePath });
  const res = fakeRes();
  await scan.handle(
    fakeReq('POST', { url: 'https://example.com/paid', payout_address: 'anim1qqqqqqqqqqqqqqqqqqqqqqqq' }),
    res, new URL('http://x/x402/bounty/claim'), '/x402/bounty/claim', anmPrice
  );
  assert.equal(res.statusCode, 201);
  assert.equal(res.json.status, 'verified');
  assert.ok(Number(res.json.amount_anm.split('.')[0]) > 13000, 'about $1 worth of ANM');
  // and it is now RESERVED against the treasury for the next claim
  assert.ok(store.reservedBountyNanm().nanm > 0n);
  store.close();
});

test('bounty: refuses a claim pointing at OUR OWN payTo address', async () => {
  const cfg = cfgFor({ bountyMode: 'open' });
  const store = createGatewayStore(':memory:');
  const scan = createScanService({
    cfg, gatewayStore: store, node: fakeNode(500000n * 1000000000n),
    fetchImpl: async () => res402([{ network: 'animica:1', payTo: cfg.anmPayTo }]),
  });
  const res = fakeRes();
  await scan.handle(
    fakeReq('POST', { url: 'https://example.com/paid', payout_address: 'anim1qqqqqqqqqqqqqqqqqqqqqqqq' }),
    res, new URL('http://x/x402/bounty/claim'), '/x402/bounty/claim', createAnmPrice({ path: cfg.anmPricePath })
  );
  assert.equal(res.statusCode, 422);
  assert.match(res.json.detail, /own address/i);
  store.close();
});

test('bounty: one open claim per host, enforced by the database', async () => {
  const store = createGatewayStore(':memory:');
  const mk = (id, host) => store.putBountyClaim({
    claimId: id, url: `https://${host}/a`, host, payoutAddress: 'anim1x',
    amountUsd: '1.00', amountNanm: '13750000000000', rateUsdAnm: '0.00007271', status: 'pending',
  });
  assert.equal(mk('c1', 'dup.example').ok, true);
  assert.equal(mk('c2', 'dup.example').ok, false, 'second claim for the same host is refused');
  assert.equal(mk('c3', 'other.example').ok, true);
  store.close();
});

// ---------------------------------------------------------------------------
// Lease: overselling the stream must be impossible.
// ---------------------------------------------------------------------------

test('lease: is DISABLED by default — a paid share of future rewards is an opt-in decision', () => {
  const cfg = cfgMod.loadGatewayConfig({}, {});
  assert.equal(cfg.leaseEnabled, false);
});

test('lease: overlapping windows cannot exceed the ceiling, even concurrently', () => {
  const store = createGatewayStore(':memory:');
  const L = (id, bps, a, b) => ({
    leaseId: id, buyerAddress: 'anim1b', shareBps: bps, startHeight: a, endHeight: b,
    paidUsd: '0.50', quotedNanm: '1', rateUsdAnm: '0.00007271',
  });
  assert.equal(store.sellLeaseIfRoom({ maxBps: 5000, lease: L('a', 3000, 100, 200) }).ok, true);
  assert.equal(store.sellLeaseIfRoom({ maxBps: 5000, lease: L('b', 2000, 150, 250) }).ok, true);
  const over = store.sellLeaseIfRoom({ maxBps: 5000, lease: L('c', 1, 160, 170) });
  assert.equal(over.ok, false);
  assert.equal(over.reason, 'oversubscribed');
  assert.equal(over.availableBps, 0);
  // a window that does NOT overlap is unaffected
  assert.equal(store.sellLeaseIfRoom({ maxBps: 5000, lease: L('d', 5000, 300, 400) }).ok, true);
  store.close();
});

test('lease: quotes are sized from the discounted price and refuse a too-short window', async () => {
  const cfg = cfgFor();
  const store = createGatewayStore(':memory:');
  const anmPrice = createAnmPrice({ path: cfg.anmPricePath });
  const p = createLeaseProduct({ cfg, node: fakeNode(0n, 76800), gatewayStore: store, anmPrice });
  p.priceUsd = cfg.leasePriceUsd;

  // $0.50 at -10% is ~6188 ANM. At 75 ANM/block a 100-block window is only
  // 7500 ANM total, so the required share is a large fraction of the ceiling.
  const ctx = { json: { blocks: 10000, payout_address: 'anim1qqqqqqqqqqqqqqqqqqqqqqqq' }, query: new URLSearchParams() };
  ctx.params = p.validate(ctx);
  const pinned = await p.preSettle.call(p, ctx);
  assert.ok(pinned.shareBps >= 1 && pinned.shareBps <= 5000, `share ${pinned.shareBps} bps within ceiling`);
  assert.equal(pinned.startHeight, 76801);
  assert.equal(pinned.endHeight, 76800 + 10000);

  // A very short window cannot deliver that value inside the ceiling.
  const short = { json: { blocks: 100, payout_address: 'anim1qqqqqqqqqqqqqqqqqqqqqqqq' }, query: new URLSearchParams() };
  short.params = p.validate(short);
  await assert.rejects(() => p.preSettle.call(p, short), (e) => {
    assert.equal(e.body.error, 'window_too_short');
    assert.ok(e.body.suggestion.min_blocks_for_this_price > 100);
    return true;
  });
  store.close();
});

test('lease: a stale price makes the product unavailable rather than quoting a dead rate', async () => {
  const stalePath = path.join(fs.mkdtempSync(path.join(os.tmpdir(), 'anmp-')), 'p.json');
  fs.writeFileSync(stalePath, JSON.stringify({
    bid: 0.00007271, is_indicative: false, source: 'nonkyc', ts: Math.floor(Date.now() / 1000) - 99999,
  }));
  const cfg = cfgFor({ anmPricePath: stalePath });
  const store = createGatewayStore(':memory:');
  const p = createLeaseProduct({
    cfg, node: fakeNode(0n), gatewayStore: store,
    anmPrice: createAnmPrice({ path: stalePath }),
  });
  const a = await p.availability();
  assert.equal(a.available, false);
  assert.equal(a.reason, 'price_stale');
  store.close();
});
