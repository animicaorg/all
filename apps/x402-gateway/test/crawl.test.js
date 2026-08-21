'use strict';
/**
 * PAID CRAWL.
 *
 * The tests that matter here are not "does it charge" — they are the ones
 * that prove it does NOT charge the parties it must never charge. A crawl
 * gate that bills Googlebot costs its customer their organic traffic, and a
 * gate that bills a reader breaks their website. Both failures are silent at
 * the gateway and expensive at the site, so the guardrails are asserted
 * first and hardest.
 */

const test = require('node:test');
const assert = require('node:assert/strict');

const { createGatewayStore } = require('../src/store/gateway');
const { createCrawlGate } = require('../src/products/crawl-gate');
const { classify } = require('../src/products/crawl-classify');
const { decide } = require('../src/products/crawl-policy');
const { createCrawlTriage, parseProposal } = require('../src/products/crawl-triage');

/** A resolver where 66.249.66.1 really is Google and 1.2.3.4 only claims to be. */
const resolver = {
  async reverse(ip) {
    if (ip === '66.249.66.1') return ['crawl-66-249-66-1.googlebot.com'];
    if (ip === '1.2.3.4') return ['evil.example.com'];
    throw new Error('ENOTFOUND');
  },
  async resolve4(h) { return h.endsWith('.googlebot.com') ? ['66.249.66.1'] : ['9.9.9.9']; },
  async resolve6() { return []; },
};

function build({ price = '0.001', freePerDay = 2, unknownPolicy = 'charge' } = {}) {
  const store = createGatewayStore(':memory:');
  const gate = createCrawlGate({ cfg: { crawlOperatorShareBps: 9000 }, gatewayStore: store, resolver });
  store.putCrawlSite({
    domain: 'news.example.com',
    priceUsd: price,
    freePerDay,
    unknownPolicy,
    rateThreshold: 30,
    operatorShareBps: 9000,
    verifyToken: 'animica-paid-crawl-token',
  });
  return { store, gate };
}

const ask = (gate, over) => gate.decideRequest({
  domain: 'news.example.com', path: '/article/1', method: 'GET', ...over,
});

// ---------------------------------------------------------------- guardrails

test('a VERIFIED search crawler is free, and no site price can change that', async () => {
  const { gate } = build({ price: '1.000', freePerDay: 0 });
  const d = await ask(gate, { userAgent: 'Googlebot/2.1', ip: '66.249.66.1' });
  assert.equal(d.action, 'allow');
  assert.equal(d.reason, 'verified_search_crawler');
  assert.equal(d.guardrail, true);
  assert.equal(d.billable, false);
});

test('a FORGED search crawler is blocked, never billed — a proven liar is not a customer', async () => {
  const { gate } = build({ freePerDay: 0 });
  const d = await ask(gate, { userAgent: 'Googlebot/2.1', ip: '1.2.3.4' });
  assert.equal(d.action, 'block');
  assert.equal(d.reason, 'forged_identity');
  assert.equal(d.billable, false);
  assert.equal(d.classified_as.spoofed, true);
});

test('Google-Extended verifies as genuinely Google and STILL pays — it is training, not search', async () => {
  const { gate } = build({ freePerDay: 0 });
  const d = await ask(gate, { userAgent: 'Mozilla/5.0 (compatible; Google-Extended/1.0)', ip: '66.249.66.1' });
  assert.equal(d.classified_as.identity_verified, true);
  assert.equal(d.action, 'charge');
});

test('humans, uptime monitors and link previews are never billed', async () => {
  const { gate } = build({ freePerDay: 0 });
  for (const [ua, ip] of [
    ['Mozilla/5.0 (Macintosh) AppleWebKit/537.36 Chrome/126 Safari/537.36', '88.1.1.1'],
    ['UptimeRobot/2.0', '7.7.7.7'],
    ['Twitterbot/1.0', '9.9.9.9'],
  ]) {
    const d = await ask(gate, { userAgent: ua, ip });
    assert.equal(d.action, 'allow', `${ua} should be allowed`);
    assert.equal(d.billable, false);
    assert.equal(d.guardrail, true);
  }
});

test('robots.txt and the terms document are free to everyone, including an unpaid AI crawler', async () => {
  const { gate } = build({ freePerDay: 0 });
  for (const p of ['/robots.txt', '/.well-known/x402', '/sitemap.xml']) {
    const d = await ask(gate, { userAgent: 'GPTBot/1.2', ip: '20.1.1.1', path: p });
    assert.equal(d.action, 'allow', `${p} must be readable`);
    assert.equal(d.reason, 'protocol_free_path');
  }
});

test('an unregistered domain FAILS OPEN — we never break a site we were merely asked about', async () => {
  const { gate } = build();
  const d = await gate.decideRequest({ domain: 'nobody.example.org', userAgent: 'GPTBot/1.2', ip: '20.1.1.1' });
  assert.equal(d.action, 'allow');
  assert.equal(d.reason, 'domain_not_registered');
  assert.equal(d.registered, false);
});

// ----------------------------------------------------------- grace and money

test('an AI crawler gets its free allowance first, then a price', async () => {
  const { gate } = build({ freePerDay: 2, price: '0.002' });
  const a = await ask(gate, { userAgent: 'GPTBot/1.2', ip: '20.1.1.1' });
  const b = await ask(gate, { userAgent: 'GPTBot/1.2', ip: '20.1.1.1' });
  const c = await ask(gate, { userAgent: 'GPTBot/1.2', ip: '20.1.1.1' });
  assert.equal(a.reason, 'free_allowance');
  assert.equal(a.free_remaining, 1);
  assert.equal(b.reason, 'free_allowance');
  assert.equal(b.free_remaining, 0);
  assert.equal(c.action, 'charge');
  assert.equal(c.price_usd, '0.002');
  assert.ok(c.buy_pass.endpoint.includes('/x402/crawl/pass'));
});

test('the grace counter is per client, so one crawler cannot spend another one\'s allowance', async () => {
  const { gate } = build({ freePerDay: 1 });
  await ask(gate, { userAgent: 'GPTBot/1.2', ip: '20.1.1.1' });
  const other = await ask(gate, { userAgent: 'CCBot/2.0', ip: '30.1.1.1' });
  assert.equal(other.reason, 'free_allowance');
});

// -------------------------------------------------------------------- passes

test('a fixed-price pass buys a request budget set by the SITE\'s per-page rate', async () => {
  for (const [price, expected] of [['0.001', 1000], ['0.002', 500], ['0.020', 50]]) {
    const { gate } = build({ price });
    const bulk = gate.passProducts.find((p) => p.id === 'crawl_pass_100'); // $1.00
    const out = await bulk.handler({ json: { domain: 'news.example.com' } });
    assert.equal(out.bodyObj.requests_purchased, expected, `$1.00 at ${price}/page`);
    assert.equal(out.bodyObj.paid_usd, '1.000');
  }
});

test('a pass is spent per request, credits the operator, and dies exactly when exhausted', async () => {
  const { store, gate } = build({ price: '0.001', freePerDay: 0 });
  const small = gate.passProducts.find((p) => p.id === 'crawl_pass'); // $0.01 -> 10
  const pass = (await small.handler({ json: { domain: 'news.example.com' } })).bodyObj.pass;

  for (let i = 0; i < 10; i += 1) {
    const d = await ask(gate, { userAgent: 'GPTBot/1.2', ip: '20.1.1.1', passToken: pass });
    assert.equal(d.action, 'allow');
    assert.equal(d.reason, 'crawl_pass');
    assert.equal(d.billable, true);
    assert.equal(d.pass_remaining, 9 - i);
  }
  // Eleventh request: the budget is gone, so it is priced again.
  const after = await ask(gate, { userAgent: 'GPTBot/1.2', ip: '20.1.1.1', passToken: pass });
  assert.equal(after.action, 'charge');

  const e = store.crawlEarnings('news.example.com', 0);
  assert.equal(Number(e.billed_requests), 10);
  // 90/10 on ten tenth-of-a-cent pages.
  assert.equal(Number(e.operator_usd).toFixed(4), '0.0090');
  assert.equal(Number(e.gateway_usd).toFixed(4), '0.0010');
});

test('a pass is bound to ONE domain and is worthless anywhere else', async () => {
  const { store, gate } = build();
  store.putCrawlSite({
    domain: 'other.example.com', priceUsd: '0.001', freePerDay: 0, unknownPolicy: 'charge',
    rateThreshold: 30, operatorShareBps: 9000, verifyToken: 't2',
  });
  const small = gate.passProducts.find((p) => p.id === 'crawl_pass');
  const pass = (await small.handler({ json: { domain: 'news.example.com' } })).bodyObj.pass;
  const d = await gate.decideRequest({ domain: 'other.example.com', userAgent: 'GPTBot/1.2', ip: '20.1.1.1', passToken: pass });
  assert.notEqual(d.reason, 'crawl_pass');
  assert.equal(d.action, 'charge');
});

test('an expired pass cannot be spent', async () => {
  const { store, gate } = build({ freePerDay: 0 });
  store.putCrawlPass({
    passId: 'p_old', tokenHash: require('../src/products/crawl-gate').tokenHashOf('anmcp_expired'),
    domain: 'news.example.com', requestsTotal: 50, priceUsd: '0.001', paidUsd: '0.05',
    issuedAt: 1, expiresAt: 2,
  });
  const d = await ask(gate, { userAgent: 'GPTBot/1.2', ip: '20.1.1.1', passToken: 'anmcp_expired' });
  assert.equal(d.action, 'charge');
});

test('buying a pass for a domain with no published terms is refused, not silently sold', async () => {
  const { gate } = build();
  const small = gate.passProducts.find((p) => p.id === 'crawl_pass');
  await assert.rejects(
    () => small.handler({ json: { domain: 'nobody.example.org' } }),
    (e) => e.status === 404,
  );
});

// ------------------------------------------------------ operators pay $0 ever

test('every operator-facing route is FREE — the crawler pays, never the site', async () => {
  const { gate } = build();
  const freePaths = gate.freeRoutes.map((r) => r.path);
  for (const p of [
    '/x402/crawl/sites', '/x402/crawl/verify', '/x402/crawl/decide',
    '/x402/crawl/install', '/x402/crawl/earnings/{domain}', '/x402/crawl/{domain}',
  ]) {
    assert.ok(freePaths.includes(p), `${p} must be a FREE route`);
  }
  // And the only paid surface is the crawler's pass.
  for (const p of gate.passProducts) {
    assert.ok(p.path.startsWith('/x402/crawl/pass'), `unexpected paid path ${p.path}`);
  }
});

test('registration needs no account and returns a usable robots.txt snippet', async () => {
  const { gate } = build();
  const route = gate.freeRoutes.find((r) => r.path === '/x402/crawl/sites');
  const out = await route.handler({ json: { domain: 'Fresh.Example.COM', price_usd: '0.005' }, headers: {} });
  assert.equal(out.status, 201);
  assert.equal(out.bodyObj.cost_to_operator, 'free');
  assert.equal(out.bodyObj.site.domain, 'fresh.example.com');
  assert.equal(out.bodyObj.site.verified, false);
  assert.match(out.bodyObj.robots_txt, /X402-Price: 0\.005 USDC per page/);
  assert.ok(out.bodyObj.verification.token.startsWith('animica-paid-crawl-'));
});

test('the published price and the enforced price cannot drift — both come from the stored row', async () => {
  const { gate } = build({ price: '0.007', freePerDay: 0 });
  const terms = gate.freeRoutes.find((r) => r.path === '/x402/crawl/{domain}');
  const doc = await terms.handler({ params: { domain: 'news.example.com' }, query: new URLSearchParams(), headers: {} });
  const charged = await ask(gate, { userAgent: 'GPTBot/1.2', ip: '20.1.1.1' });
  assert.equal(doc.bodyObj.terms.price_usd_per_page, '0.007');
  assert.equal(charged.price_usd, '0.007');
  assert.match(doc.bodyObj.robots_txt, /X402-Price: 0\.007/);
});

test('an unverified domain is readable but earns nothing until it proves control', async () => {
  const { gate } = build();
  const route = gate.freeRoutes.find((r) => r.path === '/x402/crawl/earnings/{domain}');
  const out = await route.handler({ params: { domain: 'news.example.com' }, query: new URLSearchParams(), headers: {} });
  assert.equal(out.status, 200);
  assert.equal(out.bodyObj.payable, false);
  assert.match(out.bodyObj.payable_note, /verify token/);
});

// ------------------------------------------------------------- input hygiene

test('domain input is normalised and garbage is refused', async () => {
  const { gate } = build();
  assert.equal(gate.normalizeDomain('HTTPS://News.Example.com/a/b?x=1'), 'news.example.com');
  assert.equal(gate.normalizeDomain('example.com:8443'), 'example.com');
  for (const bad of ['', 'localhost', 'not a domain', '../etc/passwd', 'a..b.com']) {
    assert.throws(() => gate.normalizeDomain(bad), undefined, `${bad} must be refused`);
  }
});

// --------------------------------------------------------- the miner offload

test('UA triage runs on AICF, records provenance, and NEVER classifies anyone into a charge', async () => {
  const { store } = build();
  store.seeUnknownUa('SomeBrandNewCrawler/1.0');

  const calls = [];
  const aicf = {
    async raw(messages) {
      calls.push(messages);
      return { text: '{"label":"ai_training","operator":"Example AI","confidence":"high","why":"names itself a crawler"}', servedModel: 'animica-chat', latencyMs: 40 };
    },
    provenanceOf(served) { return { network: served === 'animica-chat' ? 'aicf' : 'fallback', served_by: served }; },
  };
  const triage = createCrawlTriage({ gatewayStore: store, aicf, cfg: {} });
  const res = await triage.runOnce({ limit: 5 });

  assert.equal(res.ok, true);
  assert.equal(res.triaged, 1);
  assert.equal(res.served_by_aicf, 1);

  const row = store.listUaProposals(5)[0];
  assert.equal(row.status, 'triaged');
  assert.equal(row.served_by, 'animica-chat');
  const proposal = JSON.parse(row.proposal_json);
  assert.equal(proposal.advisory, true);
  assert.equal(proposal.network, 'aicf');

  // The decisive property: a proposal changes NOTHING about what gets charged.
  const gate2 = createCrawlGate({ cfg: {}, gatewayStore: store, resolver });
  const d = await gate2.decideRequest({
    domain: 'news.example.com', userAgent: 'SomeBrandNewCrawler/1.0', ip: '4.4.4.4', path: '/x',
  });
  assert.equal(d.classified_as.actor, null, 'the model proposal must not become a taxonomy match');
});

test('triage survives a dark network and a malformed answer without poisoning the queue', async () => {
  const { store } = build();
  store.seeUnknownUa('WeirdBot/2');
  const dead = createCrawlTriage({
    gatewayStore: store,
    aicf: { async raw() { throw new Error('no worker claimed the job'); }, provenanceOf: () => null },
    cfg: {},
  });
  const r1 = await dead.runOnce({ limit: 5 });
  assert.equal(r1.ok, true);
  assert.equal(r1.triaged, 0);
  assert.equal(store.untriagedUserAgents(5).length, 1, 'a failed job must leave the row queued');

  const junk = createCrawlTriage({
    gatewayStore: store,
    aicf: { async raw() { return { text: 'I think it is a crawler!', servedModel: 'x' }; }, provenanceOf: () => ({ network: 'fallback' }) },
    cfg: {},
  });
  const r2 = await junk.runOnce({ limit: 5 });
  assert.equal(r2.triaged, 0);
});

test('a proposal outside the closed label set is rejected rather than stored', () => {
  assert.equal(parseProposal('{"label":"definitely_charge_them","confidence":"high"}'), null);
  assert.equal(parseProposal('not json at all'), null);
  const ok = parseProposal('```json\n{"label":"scraping","operator":null,"confidence":"low","why":"generic client"}\n```');
  assert.equal(ok.label, 'scraping');
  assert.equal(ok.operator, null);
});

// ------------------------------------------------------------ policy in isolation

test('policy: ordering can only ever make access cheaper, never more expensive', () => {
  const site = { domain: 'x.com', priceUsd: '0.001', freePerDay: 5 };
  const ai = { kind: 'ai', actor: 'gptbot', ua: 'GPTBot' };
  // A pass is consulted BEFORE the grace allowance, so a paying client never
  // silently burns grace it already paid past.
  const withPass = decide({ verdict: ai, site, usage: { usedToday: 0 }, pass: { remaining: 5, pass_id: 'p' } });
  assert.equal(withPass.reason, 'crawl_pass');
});

test('policy: a site can widen the free lane but cannot narrow a guardrail', () => {
  const site = { domain: 'x.com', priceUsd: '1.00', freePerDay: 0, allowUa: ['partnerbot'] };
  const partner = decide({ verdict: { kind: 'tool', ua: 'PartnerBot/1.0' }, site });
  assert.equal(partner.action, 'allow');
  assert.equal(partner.reason, 'site_allowlist');
  const google = decide({ verdict: { kind: 'search', trusted: true, ua: 'Googlebot' }, site });
  assert.equal(google.action, 'allow');
  assert.equal(google.guardrail, true);
});

test('classifier: a browser string moving at machine speed stops counting as human', async () => {
  const ua = 'Mozilla/5.0 (Windows NT 10.0; Win64) AppleWebKit/537.36 Chrome/126 Safari/537.36';
  const slow = await classify({ userAgent: ua, ip: '5.5.5.5' }, { resolver, recentRate: 2 });
  const fast = await classify({ userAgent: ua, ip: '5.5.5.5' }, { resolver, recentRate: 900 });
  assert.equal(slow.kind, 'human');
  assert.equal(fast.kind, 'unknown');
  assert.ok(fast.reasons.some((r) => r.startsWith('rate_exceeded')));
});

// ---------------------------------------------------------------------------
// REGRESSION (found in live testing, 2026-08-19). A forged Googlebot coming
// from an IP with NO PTR record was reaching the free search lane: spoof
// detection only fired on an ACTIVE contradiction, and a lookup that simply
// failed fell through to "unverified search crawler — allow". That made the
// literal string "Googlebot" worth free access to anyone willing to type it,
// which is the whole product defeated by the most obvious possible evasion.
// ---------------------------------------------------------------------------

const { forwardConfirmedRdns } = require('../src/products/crawl-classify');

test('a claimed Googlebot from an IP with NO reverse record is a spoof, not an unverified guest', async () => {
  const noPtr = {
    async reverse() { const e = new Error('getHostByAddr ENOTFOUND'); e.code = 'ENOTFOUND'; throw e; },
    async resolve4() { return []; },
    async resolve6() { return []; },
  };
  const store = createGatewayStore(':memory:');
  const gate = createCrawlGate({ cfg: {}, gatewayStore: store, resolver: noPtr });
  store.putCrawlSite({
    domain: 'news.example.com', priceUsd: '0.001', freePerDay: 0, unknownPolicy: 'charge',
    rateThreshold: 30, operatorShareBps: 9000, verifyToken: 't',
  });
  const d = await gate.decideRequest({ domain: 'news.example.com', userAgent: 'Googlebot/2.1', ip: '1.2.3.4', path: '/a' });
  assert.equal(d.action, 'block');
  assert.equal(d.reason, 'forged_identity');
});

test('a DNS outage on OUR side degrades a search claim to charged — never to free, never to blocked', async () => {
  const brokenDns = {
    async reverse() { const e = new Error('queryPtr ETIMEOUT'); e.code = 'ETIMEOUT'; throw e; },
    async resolve4() { return []; },
    async resolve6() { return []; },
  };
  const store = createGatewayStore(':memory:');
  const gate = createCrawlGate({ cfg: {}, gatewayStore: store, resolver: brokenDns });
  store.putCrawlSite({
    domain: 'news.example.com', priceUsd: '0.001', freePerDay: 0, unknownPolicy: 'charge',
    rateThreshold: 30, operatorShareBps: 9000, verifyToken: 't',
  });
  const d = await gate.decideRequest({ domain: 'news.example.com', userAgent: 'Googlebot/2.1', ip: '66.249.66.1', path: '/a' });
  assert.equal(d.action, 'charge', 'a resolver outage must not block a real search crawler');
  assert.ok(d.classified_as.signals.includes('verification_unavailable_degraded'));
});

test('a search crawler with no published verification method keeps the free lane', async () => {
  const { gate } = build({ freePerDay: 0 });
  const d = await ask(gate, { userAgent: 'DuckDuckBot/1.1', ip: '5.5.5.5' });
  assert.equal(d.action, 'allow');
  assert.equal(d.reason, 'search_crawler_no_published_method');
});

test('rDNS helper reports whose failure it was', async () => {
  const notFound = {
    async reverse() { const e = new Error('nope'); e.code = 'ENOTFOUND'; throw e; },
    async resolve4() { return []; }, async resolve6() { return []; },
  };
  const timeout = {
    async reverse() { const e = new Error('nope'); e.code = 'ETIMEOUT'; throw e; },
    async resolve4() { return []; }, async resolve6() { return []; },
  };
  assert.equal((await forwardConfirmedRdns('1.2.3.4', ['.googlebot.com'], { resolver: notFound })).reason, 'rdns_no_ptr');
  assert.equal((await forwardConfirmedRdns('1.2.3.4', ['.googlebot.com'], { resolver: timeout })).reason, 'rdns_unavailable');
});

// ---------------------------------------------------------------------------
// POST-QUANTUM CRAWL LICENCES (ML-DSA-65).
//
// The pass token is an HMAC and proves things only to us. The licence is the
// artefact an AI company can show someone else — so the tests that matter are
// that it attests to STORED state rather than to whatever the buyer claimed,
// and that altering a single field breaks the signature.
// ---------------------------------------------------------------------------

const { createCrawlLicence, canonicalJson } = require('../src/products/crawl-licence');
const { tokenHashOf } = require('../src/products/crawl-gate');

const LICENCE_KEY = require('node:path').join(
  require('node:os').tmpdir(), `animica-crawl-licence-test-${process.pid}.json`,
);

function licenceFixture() {
  const store = createGatewayStore(':memory:');
  store.putCrawlSite({
    domain: 'news.example.com', priceUsd: '0.002', freePerDay: 100, unknownPolicy: 'charge',
    rateThreshold: 30, operatorShareBps: 9000, verifyToken: 't',
  });
  store.markCrawlSiteVerified('news.example.com');
  const at = Math.floor(Date.now() / 1000);
  store.putCrawlPass({
    passId: 'pass123', tokenHash: tokenHashOf('anmcp_demo'), domain: 'news.example.com',
    requestsTotal: 500, priceUsd: '0.002', paidUsd: '1.000', payer: '0xBuyer',
    issuedAt: at, expiresAt: at + 3600,
  });
  for (let i = 0; i < 7; i += 1) {
    store.spendCrawlPass({
      tokenHash: tokenHashOf('anmcp_demo'), domain: 'news.example.com', actor: 'gptbot',
      operator: 'OpenAI', kind: 'ai', path: '/a', priceUsd: '0.002', operatorShareBps: 9000,
    });
  }
  const cfg = require('../src/config').loadGatewayConfig({}, {});
  const lic = createCrawlLicence({ cfg: { ...cfg, crawlLicenceKeyPath: LICENCE_KEY }, gatewayStore: store });
  return { store, lic };
}

test('a crawl licence is ML-DSA-65 signed, verifies, and dies on a single altered character', async (t) => {
  const { lic } = licenceFixture();
  const issue = lic.freeRoutes.find((r) => r.path === '/x402/crawl/licence');
  let out;
  try {
    out = await issue.handler({ json: { pass: 'anmcp_demo' }, headers: {} });
  } catch (_e) {
    return t.skip('post-quantum signer unavailable on this host');
  }
  if (out.status === 503) return t.skip('post-quantum signer unavailable on this host');

  assert.equal(out.status, 200);
  assert.equal(out.bodyObj.cost, 'free');
  assert.equal(out.bodyObj.signature.alg_id, 4099);
  assert.equal(out.bodyObj.signature.alg, 'ml_dsa_65');
  assert.equal(out.bodyObj.signature.domain, 'animica-paid-crawl');

  // It attests to STORED consumption, not to anything the caller asserted.
  assert.equal(out.bodyObj.licence.pages_consumed, 7);
  assert.equal(out.bodyObj.licence.pages_licensed, 500);
  assert.equal(out.bodyObj.licence.site_owner_verified, true);

  const verifyRoute = lic.freeRoutes.find((r) => r.path === '/x402/crawl/licence/verify');
  const good = await verifyRoute.handler({ json: { signed_bytes: out.bodyObj.signed_bytes, signature: out.bodyObj.signature }, headers: {} });
  assert.equal(good.bodyObj.ok, true);
  assert.equal(good.bodyObj.issued_by_this_gateway, true);

  const tampered = out.bodyObj.signed_bytes.replace('"pages_consumed":7', '"pages_consumed":99999');
  assert.notEqual(tampered, out.bodyObj.signed_bytes, 'the tamper must actually change the bytes');
  const bad = await verifyRoute.handler({ json: { signed_bytes: tampered, signature: out.bodyObj.signature }, headers: {} });
  assert.equal(bad.bodyObj.ok, false);
});

test('a licence for an unknown pass is refused rather than invented', async () => {
  const { lic } = licenceFixture();
  const issue = lic.freeRoutes.find((r) => r.path === '/x402/crawl/licence');
  const out = await issue.handler({ json: { pass: 'anmcp_nosuchpass' }, headers: {} });
  assert.equal(out.status, 404);
  assert.equal(out.bodyObj.error, 'unknown_pass');
});

test('verification is FREE and needs no pass — evidence nobody can check is not evidence', async () => {
  const { lic } = licenceFixture();
  const paths = lic.freeRoutes.map((r) => r.path);
  for (const p of ['/x402/crawl/licence', '/x402/crawl/licence/verify', '/x402/crawl/pubkey']) {
    assert.ok(paths.includes(p), `${p} must be free`);
  }
});

test('canonical JSON is stable under key order, so the signature covers meaning not formatting', () => {
  assert.equal(canonicalJson({ b: 1, a: 2 }), canonicalJson({ a: 2, b: 1 }));
  assert.notEqual(canonicalJson({ a: 1 }), canonicalJson({ a: 2 }));
});

test('sibling endpoints under /x402/crawl/ are never captured as domain names', () => {
  const store = createGatewayStore(':memory:');
  const gate = createCrawlGate({ cfg: {}, gatewayStore: store });
  const terms = gate.freeRoutes.find((r) => r.path === '/x402/crawl/{domain}');
  // Every free route that lives under /x402/crawl/<one-segment> must be
  // excluded, or the greedy terms route answers 400 for a real endpoint.
  for (const r of gate.freeRoutes) {
    const m = /^\/x402\/crawl\/([^/]+)$/.exec(r.path);
    if (!m || m[1].startsWith('{')) continue;
    assert.equal(terms.match(r.path), null, `${r.path} must not be captured as a domain`);
  }
  for (const reserved of ['/x402/crawl/pubkey', '/x402/crawl/licence', '/x402/crawl/install']) {
    assert.equal(terms.match(reserved), null, `${reserved} must not be captured as a domain`);
  }
  assert.deepEqual(terms.match('/x402/crawl/news.example.com'), { domain: 'news.example.com' });
});

// ---------------------------------------------------------------------------
// DIRECTORY-LISTING QUALITY.
//
// x402scan and Bazaar index us from our OpenAPI document, so a defect there is
// not cosmetic — it is what a buyer sees, or the reason a resource is rejected
// outright. Two real ones were shipped and caught only by reading the
// published document back:
//
//   1. The three pass products declared their input as a raw JSON Schema, but
//      discovery/openapi.js builds requestBody from `bodyFields` and ignores
//      anything else. Result: OpenAPI operations with NO request body, which
//      is precisely the "Missing input schema" x402scan rejects on. The 402
//      challenge looked perfect the whole time, so nothing else caught it.
//   2. Every free route inherits its summary from its PARENT product, and all
//      twelve Paid Crawl routes hang off the pass product — so the directory
//      listed "Crawl pass (small) — free disclosure" twelve times, for the
//      decision endpoint, the install guide, the pubkey, everything.
// ---------------------------------------------------------------------------

const { buildTestGateway, request } = require('./gateway-helpers');

/** The OpenAPI document AS SERVED — the exact bytes an indexer ingests. */
async function servedOpenApi(t) {
  return (await request(t.baseUrl, '/x402/openapi.json')).json;
}

test('every POST product publishes a requestBody — the exact thing indexers reject us for', async () => {
  const { buildTestGateway } = require('./gateway-helpers');
  const t = await buildTestGateway();
  try {
    const doc = await servedOpenApi(t);
    const missing = [];
    for (const p of t.gw.registry.products) {
      for (const r of p.routes || []) {
        if (r.method !== 'POST') continue;
        const tmpl = r.path.replace(/\{[^}]+\}/g, (m) => m);
        const op = doc.paths[tmpl] && doc.paths[tmpl].post;
        if (!op) continue;
        // A product with no declared input legitimately has no body.
        const declared = p.outputSchema && p.outputSchema.input;
        const wantsBody = !!(declared && (declared.bodyFields || declared.schema));
        if (wantsBody && !op.requestBody) missing.push(`${p.id} ${r.path}`);
      }
    }
    assert.deepEqual(missing, [],
      `these POST products declare an input but publish no OpenAPI requestBody — indexers reject them as "Missing input schema": ${missing.join(', ')}`);
  } finally {
    await t.close();
  }
});

test('the three crawl passes each publish a domain requestBody', async () => {
  const { buildTestGateway } = require('./gateway-helpers');
  const t = await buildTestGateway();
  try {
    const doc = await servedOpenApi(t);
    for (const p of ['/x402/crawl/pass', '/x402/crawl/pass/10', '/x402/crawl/pass/100']) {
      const op = doc.paths[p] && doc.paths[p].post;
      assert.ok(op, `${p} missing from OpenAPI`);
      const schema = op.requestBody
        && op.requestBody.content
        && op.requestBody.content['application/json']
        && op.requestBody.content['application/json'].schema;
      assert.ok(schema, `${p} publishes no requestBody schema`);
      assert.ok(schema.properties && schema.properties.domain, `${p} requestBody has no domain field`);
      assert.deepEqual(schema.required, ['domain'], `${p} must require domain`);
    }
  } finally {
    await t.close();
  }
});

test('no two endpoints share an OpenAPI summary — a directory would list them identically', async () => {
  const { buildTestGateway } = require('./gateway-helpers');
  const t = await buildTestGateway();
  try {
    const doc = await servedOpenApi(t);
    // Scoped to FREE routes. A paid product legitimately appears under both
    // GET and POST with one summary — that is one resource, two methods. Free
    // routes are distinct endpoints that merely happen to hang off a shared
    // parent product, so two of them sharing a summary is always the
    // inherited-title defect and never a legitimate alias.
    const seen = new Map();
    const dupes = [];
    for (const [p, ops] of Object.entries(doc.paths)) {
      for (const [m, op] of Object.entries(ops)) {
        if (!op || !op.summary) continue;
        const info = op['x-payment-info'];
        if (!info || info.free !== true) continue;
        const where = `${m.toUpperCase()} ${p}`;
        if (seen.has(op.summary)) dupes.push(`"${op.summary}" on both ${seen.get(op.summary)} and ${where}`);
        else seen.set(op.summary, where);
      }
    }
    assert.deepEqual(dupes, [], `two free endpoints would be listed identically:\n  ${dupes.join('\n  ')}`);
  } finally {
    await t.close();
  }
});

test('only the commitment reveal claims reveal semantics — nothing else is "still sealed"', async () => {
  const { buildTestGateway } = require('./gateway-helpers');
  const t = await buildTestGateway();
  try {
    const doc = await servedOpenApi(t);
    const sealed = [];
    for (const [p, ops] of Object.entries(doc.paths)) {
      for (const [m, op] of Object.entries(ops)) {
        if (op && op.responses && op.responses['425']) sealed.push(`${m.toUpperCase()} ${p}`);
      }
    }
    assert.deepEqual(sealed, ['GET /x402/random/reveal/{commit_id}'],
      'a 425 "still sealed" response belongs to the commit-reveal product alone');
  } finally {
    await t.close();
  }
});

test('every Paid Crawl endpoint names itself in the directory', async () => {
  const { buildTestGateway } = require('./gateway-helpers');
  const t = await buildTestGateway();
  try {
    const doc = await servedOpenApi(t);
    for (const [p, ops] of Object.entries(doc.paths)) {
      if (!p.startsWith('/x402/crawl')) continue;
      for (const [m, op] of Object.entries(ops)) {
        if (!op || !op.summary) continue;
        assert.ok(
          /Paid Crawl|Crawl pass|Fetch & extract/.test(op.summary),
          `${m.toUpperCase()} ${p} is listed as "${op.summary}" — it must name itself`,
        );
      }
    }
  } finally {
    await t.close();
  }
});

// ---------------------------------------------------------------------------
// THE ROOT SPEC MUST NOT GO STALE.
//
// x402scan (and other crawlers) discover an origin by reading the ORIGIN-ROOT
// /openapi.json and ignore both /.well-known/x402 and /x402/openapi.json. That
// root file is generated by bin/regen-root-openapi.py as a manual step, and on
// 2026-08-19 it had not been re-run since two product families shipped. The
// consequence was invisible from every surface we normally check: the catalog
// was right, the gateway spec was right, every endpoint answered 402 correctly,
// and x402scan's own registrar reported "registered 45, failed 0" — while
// silently storing none of the six new products, because they were absent from
// the only document it actually reads. The real error surfaced only from the
// per-resource endpoint: "not listed in the origin's openapi.json".
//
// This test runs only on a host that serves that file. Elsewhere it skips
// rather than failing, because the file is deploy state, not repo state.
// ---------------------------------------------------------------------------

test('the origin-root openapi.json lists every paid path — crawlers read only that one', async (t) => {
  const fsx = require('node:fs');
  const ROOT = '/var/www/animica.dev/openapi.json';
  if (!fsx.existsSync(ROOT)) return t.skip('not the deploy host — the root spec is deploy state');

  // Compare deploy state against deploy state: the LIVE gateway's own spec
  // against the root file crawlers read. Using the test gateway here would
  // compare test config to production and flag products that production
  // legitimately disables (observed: /x402/mining/lease).
  let live;
  try {
    const res = await fetch('http://127.0.0.1:8742/x402/openapi.json', { signal: AbortSignal.timeout(5000) });
    if (!res.ok) return t.skip(`local gateway answered ${res.status}`);
    live = await res.json();
  } catch (e) {
    return t.skip(`local gateway not reachable: ${e.message}`);
  }

  let root;
  try {
    root = JSON.parse(fsx.readFileSync(ROOT, 'utf8'));
  } catch (e) {
    return t.skip(`root spec unreadable: ${e.message}`);
  }

  // Paid paths only: the generator deliberately omits free routes, trials and
  // discovery surfaces, and a crawler only needs the payable ones.
  const paid = Object.entries(live.paths)
    .filter(([p, ops]) => p.startsWith('/x402')
      && Object.values(ops).some((o) => o && o['x-payment-info'] && o['x-payment-info'].free !== true))
    .map(([p]) => p);
  const rootPaths = new Set(Object.keys(root.paths || {}));
  const missing = paid.filter((p) => !rootPaths.has(p));

  assert.deepEqual(missing, [],
    `these paid paths are live but absent from ${ROOT}, so crawlers cannot register them — `
    + `re-run bin/regen-root-openapi.py: ${missing.join(', ')}`);
});

// ---------------------------------------------------------------------------
// Two defects that only a REAL settled payment surfaced (2026-08-19):
//
//   1. Every licence was issued with "payer": null. execute-then-settle runs
//      the product handler BEFORE settlement, so ctx.settlement is still null
//      there — and ctx carried no payer at all. A provenance receipt that
//      cannot name who bought the access is most of the way to worthless.
//   2. /x402/crawl/licence/verify 502'd on every genuine licence. Free routes
//      inherited the parent product's 4KB body cap, and a licence is ~11KB by
//      construction (6.6KB signature hex + 3.9KB public key hex). It passed
//      every hand-made test payload, which were all small.
// ---------------------------------------------------------------------------

test('the licence verify route raises its own body cap above the parent product', () => {
  const store = createGatewayStore(':memory:');
  const cfg = require('../src/config').loadGatewayConfig({}, {});
  const lic = createCrawlLicence({ cfg, gatewayStore: store });
  const verify = lic.freeRoutes.find((r) => r.path === '/x402/crawl/licence/verify');
  // A real licence body: 3309-byte signature + 1952-byte key, both hex, in JSON.
  const realistic = 3309 * 2 + 1952 * 2 + 500;
  assert.ok(verify.maxBodyBytes >= realistic,
    `verify caps at ${verify.maxBodyBytes} bytes but a genuine licence is ~${realistic}`);

  const gate = createCrawlGate({ cfg, gatewayStore: store });
  const parentCap = gate.passProducts[0].maxBodyBytes;
  assert.ok(verify.maxBodyBytes > parentCap,
    'the verify route must not inherit the pass product cap it would exceed');
});

test('a paid handler can see the verified payer before settlement', () => {
  // The contract the licence depends on: paywall.js sets ctx.payer from the
  // verification verdict, which is established before execute-then-settle runs
  // the handler. Asserted against the source so a refactor that drops it fails
  // here rather than silently reintroducing "payer": null on every receipt.
  const src = require('node:fs').readFileSync(require('node:path').join(__dirname, '..', 'src', 'paywall.js'), 'utf8');
  assert.match(src, /ctx\.payer\s*=\s*payer/,
    'paywall.js must expose the verified payer on ctx for execute-then-settle products');
  // Both payment paths must name a buyer before the handler runs: the on-chain
  // lane from the verification verdict, and the prepaid-credits lane from the
  // voucher id. A licence bought with credits must not silently lose its payer.
  const onchain = src.indexOf('ctx.payer = payer');
  const credits = src.indexOf('ctx.payer = `voucher:');
  assert.ok(credits > -1, 'the prepaid-credits path must identify its buyer too');
  const handlers = [];
  for (let i = src.indexOf('await product.handler(ctx)'); i !== -1; i = src.indexOf('await product.handler(ctx)', i + 1)) handlers.push(i);
  assert.ok(handlers.length >= 2, 'expected both the credits and settlement handler paths');
  assert.ok(credits < handlers[0], 'the credits path must set ctx.payer before its handler call');
  assert.ok(onchain > -1 && onchain < handlers[handlers.length - 1],
    'the settlement path must set ctx.payer before its handler call');
});

test('the free setup is discoverable by an agent without paying anything', async () => {
  // Every other discovery surface we publish lists things that cost money. An
  // agent acting for a WEBSITE OWNER must be able to find the half that is
  // free — otherwise it sees three passes for sale and no way to learn that
  // setting up the selling side costs nothing.
  const store = createGatewayStore(':memory:');
  const gate = createCrawlGate({ cfg: { crawlOperatorShareBps: 9000 }, gatewayStore: store });
  const wk = gate.freeRoutes.find((r) => r.path === '/.well-known/paid-crawl');
  assert.ok(wk, 'the protocol discovery document must exist');

  const out = await wk.handler({ headers: {}, query: new URLSearchParams(), params: {} });
  assert.equal(out.status, 200);
  const d = out.bodyObj;
  assert.equal(d.protocol, 'animica.paid-crawl/v1');
  assert.match(d.operator_cost, /free/i);
  assert.equal(d.for_website_owners.cost, 'free');
  assert.equal(d.for_website_owners.account_required, false);
  assert.equal(d.for_website_owners.api_key_required, false);
  assert.ok(d.for_website_owners.steps.length >= 4, 'the owner path must be spelled out end to end');
  assert.ok(d.for_crawlers.steps.length >= 3, 'the crawler path must be spelled out too');

  // The document must not quietly oversell: the demand caveat is load-bearing.
  assert.match(d.honest_note, /do not implement x402|blocked/i);

  // Every endpoint it advertises as free must actually be a free route.
  const freePaths = new Set(gate.freeRoutes.map((r) => r.path));
  const licencePaths = new Set(['POST /x402/crawl/licence', 'POST /x402/crawl/licence/verify', 'GET /x402/crawl/pubkey']);
  for (const entry of d.free_endpoints) {
    if (licencePaths.has(entry)) continue; // lives on the licence module
    const p = entry.split(' ')[1];
    assert.ok(freePaths.has(p), `${entry} is advertised as free but is not a free route`);
  }
  // And everything it calls paid must actually be a paid product.
  const paidPaths = new Set(gate.passProducts.map((p) => p.path));
  for (const entry of d.paid_endpoints) {
    assert.ok(paidPaths.has(entry.split(' ')[1]), `${entry} is advertised as paid but is not a product`);
  }
});
