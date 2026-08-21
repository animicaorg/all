'use strict';
/**
 * GEO audit tests.
 *
 * Two things here are worth more than the rest: that the robots.txt parser
 * follows the grouping and specificity rules real crawlers use (a naive parser
 * reports the wrong agents as allowed, which would make the headline finding
 * of this product simply false), and that an agent robots.txt forbids is never
 * probed — sending that request would mean identifying as a crawler the site
 * asked to stay away, to learn something robots.txt already told us.
 */

const { test } = require('node:test');
const assert = require('node:assert/strict');

const {
  createGeoAuditProduct, parseRobots, robotsVerdict, pathMatches, extractJsonLd,
} = require('../src/products/geo');
const { loadGatewayConfig } = require('../src/config');

const cfg = loadGatewayConfig(process.env);

/** A public address, so the SSRF guard lets the fixture host through. */
const publicLookup = async () => [{ address: '93.184.216.34', family: 4 }];

function res(status, body, contentType = 'text/html') {
  const buf = Buffer.from(body);
  return {
    ok: status >= 200 && status < 300,
    status,
    url: 'https://example.com/',
    headers: { get: (h) => (h.toLowerCase() === 'content-type' ? contentType : null) },
    body: (async function* () { yield buf; })(),
  };
}


/**
 * Run the handler and unwrap the delivery envelope, asserting its shape.
 * A handler that returns the payload bare produced a fully charged 200 with an
 * empty body in production, so the envelope is pinned here, not assumed.
 */
async function audit(p, url = 'https://example.com/') {
  const out = await p.handler({ params: p.validate({ json: { url } }) });
  assert.equal(out.status, 200);
  assert.ok(out.bodyObj && typeof out.bodyObj === 'object',
    'a product handler must return { status, bodyObj } — a bare payload is delivered as an empty body');
  return out.bodyObj;
}

// ---------------------------------------------------------------------------
// robots.txt
// ---------------------------------------------------------------------------

test('parseRobots: consecutive User-agent lines share one rule block', () => {
  // The trap: keeping only the last agent reports GPTBot as allowed here.
  const groups = parseRobots([
    'User-agent: GPTBot',
    'User-agent: CCBot',
    'Disallow: /',
    '',
    'User-agent: *',
    'Disallow: /admin',
  ].join('\n'));
  assert.equal(groups.length, 2);
  assert.deepEqual(groups[0].agents, ['gptbot', 'ccbot']);
  assert.equal(robotsVerdict(groups, 'GPTBot', '/').allowed, false);
  assert.equal(robotsVerdict(groups, 'CCBot', '/').allowed, false);
  assert.equal(robotsVerdict(groups, 'PerplexityBot', '/').allowed, true, 'falls back to * which only blocks /admin');
});

test('robotsVerdict: an agent-specific group overrides a blanket wildcard', () => {
  const groups = parseRobots(['User-agent: *', 'Disallow: /', '', 'User-agent: ClaudeBot', 'Disallow:'].join('\n'));
  assert.equal(robotsVerdict(groups, 'ClaudeBot', '/').allowed, true, 'empty Disallow means allow everything');
  assert.equal(robotsVerdict(groups, 'ClaudeBot', '/').specific, true);
  assert.equal(robotsVerdict(groups, 'GPTBot', '/').allowed, false);
});

test('robotsVerdict: longest match wins, and Allow breaks a tie', () => {
  const groups = parseRobots(['User-agent: *', 'Disallow: /blog', 'Allow: /blog/public'].join('\n'));
  assert.equal(robotsVerdict(groups, 'GPTBot', '/blog/private').allowed, false);
  assert.equal(robotsVerdict(groups, 'GPTBot', '/blog/public/post').allowed, true);
  const tie = parseRobots(['User-agent: *', 'Disallow: /x', 'Allow: /x'].join('\n'));
  assert.equal(robotsVerdict(tie, 'GPTBot', '/x').allowed, true, 'equal length: Allow wins');
});

test('pathMatches: wildcards and the $ anchor', () => {
  assert.equal(pathMatches('/*.pdf$', '/docs/a.pdf'), true);
  assert.equal(pathMatches('/*.pdf$', '/docs/a.pdf?x=1'), false);
  assert.equal(pathMatches('/api/', '/api/v1/thing'), true);
  assert.equal(pathMatches('', '/anything'), false, 'an empty pattern must match nothing');
});

// ---------------------------------------------------------------------------
// JSON-LD
// ---------------------------------------------------------------------------

test('extractJsonLd: flattens @graph and reports unparseable blocks rather than skipping them', () => {
  const html = `
    <script type="application/ld+json">{"@graph":[{"@type":"Organization"},{"@type":"WebSite"}]}</script>
    <script type="application/ld+json">{ not json }</script>`;
  const types = extractJsonLd(html).map((n) => n['@type']);
  assert.ok(types.includes('Organization') && types.includes('WebSite'));
  assert.ok(types.includes('__invalid__'), 'a broken block must surface — silently dropping it hides a real defect');
});

// ---------------------------------------------------------------------------
// Probe behaviour
// ---------------------------------------------------------------------------

function fixtureFetch(robotsBody, { onAgentProbe = () => {}, homeStatusFor = () => 200 } = {}) {
  const seen = [];
  const impl = async (url, init) => {
    const ua = (init && init.headers && init.headers['user-agent']) || '';
    const path = new URL(url).pathname;
    seen.push({ path, ua });
    if (path === '/robots.txt') return res(200, robotsBody, 'text/plain');
    if (path === '/') {
      if (!ua.startsWith('AnimicaGeoAudit')) {
        onAgentProbe(ua);
        return res(homeStatusFor(ua), 'blocked', 'text/html');
      }
      return res(200, '<html><head><title>T</title><meta name="description" content="D"></head><body><h1>H</h1><p>'
        + 'real readable prose. '.repeat(80) + '</p></body></html>');
    }
    return res(404, 'nope', 'text/plain');
  };
  return { impl, seen };
}

test('an agent robots.txt disallows is never probed', async () => {
  const { impl, seen } = fixtureFetch(['User-agent: GPTBot', 'Disallow: /'].join('\n'));
  const p = createGeoAuditProduct({ cfg, fetchImpl: impl, lookup: publicLookup });
  const r = await audit(p);

  const gptProbes = seen.filter((s) => s.path === '/' && s.ua.includes('GPTBot'));
  assert.equal(gptProbes.length, 0, 'a forbidden agent must not be impersonated to confirm what robots.txt already says');

  const gpt = r.crawler_access.find((c) => c.agent === 'GPTBot');
  assert.equal(gpt.verdict, 'blocked_by_robots');
  assert.equal(gpt.http_status, null);
  assert.ok(r.crawler_probes_skipped_by_robots >= 1);
  // The others are permitted, so they ARE probed.
  assert.ok(seen.some((s) => s.path === '/' && s.ua.includes('ClaudeBot')));
});

test('a 429 to a robots-permitted agent is the top finding', async () => {
  const { impl } = fixtureFetch('User-agent: *\nDisallow:\n', {
    homeStatusFor: (ua) => (ua.includes('PerplexityBot') ? 429 : 200),
  });
  const p = createGeoAuditProduct({ cfg, fetchImpl: impl, lookup: publicLookup });
  const r = await audit(p);

  const px = r.crawler_access.find((c) => c.agent === 'PerplexityBot');
  assert.equal(px.verdict, 'rate_limited');
  assert.match(px.detail, /contradicting your own stated policy/);
  assert.equal(r.fixes[0].id, 'ai_crawler_access', 'crawler access outranks every other fix by design');
  assert.ok(r.fixes[0].points_recoverable > 0);
});

test('the score is deterministic for an unchanged site', async () => {
  const mk = () => createGeoAuditProduct({ cfg, fetchImpl: fixtureFetch('User-agent: *\nDisallow:\n').impl, lookup: publicLookup });
  const run = async () => {
    const p = mk();
    return (await audit(p)).score;
  };
  const [a, b] = [await run(), await run()];
  assert.equal(a, b, 'a score that moves when the site did not is a broken score');
});

test('an unreachable origin is refused, not charged for', async () => {
  const impl = async () => { throw new Error('ECONNREFUSED'); };
  const p = createGeoAuditProduct({ cfg, fetchImpl: impl, lookup: publicLookup });
  await assert.rejects(
    () => p.handler({ params: p.validate({ json: { url: 'https://example.com/' } }) }),
    (e) => {
      assert.equal(e.body.error, 'origin_unreachable');
      assert.match(e.body.detail, /Nothing was charged/);
      return true;
    },
  );
});

test('a private or loopback target is refused before any request goes out', async () => {
  let called = 0;
  const impl = async () => { called++; return res(200, 'x'); };
  const p = createGeoAuditProduct({
    cfg, fetchImpl: impl, lookup: async () => [{ address: '127.0.0.1', family: 4 }],
  });
  await assert.rejects(() => p.handler({ params: p.validate({ json: { url: 'https://internal.example/' } }) }));
  assert.equal(called, 0, 'the SSRF guard must run before the socket, not after');
});

test('validate rejects anything that is not an absolute http(s) URL', () => {
  const p = createGeoAuditProduct({ cfg, fetchImpl: async () => res(200, ''), lookup: publicLookup });
  for (const url of ['', 'not a url', 'file:///etc/passwd', 'ftp://x.example/']) {
    assert.throws(() => p.validate({ json: { url } }), `should reject ${JSON.stringify(url)}`);
  }
  assert.doesNotThrow(() => p.validate({ json: { url: 'https://example.com/path' } }));
});

// ---------------------------------------------------------------------------
// Trial parameter passing.
//
// Found live: every /trial route dropped the caller's validated input, because
// the trial spread the fields flat while every product reads ctx.params (which
// is what the paid path sets). Trials did not fail — they answered the wrong
// question with defaults, which is the worst way for a free sample to behave.
// ---------------------------------------------------------------------------

// Found live on /x402/chain/export/trial: the free-route ctx has no `route`,
// but products read it (bulk-chain picks its export type from ctx.route.path),
// so validate() threw a TypeError INSIDE the trial. Two defects, one symptom —
// the missing ctx key, and a catch that called our own crash `invalid_request`
// and echoed the raw JS message ("Cannot read properties of undefined") to the
// caller, sending an agent off to fix a request that was never wrong.
test('a trial gives validate() the paid route in ctx, as the paywall does', async () => {
  const { createTrialRoute } = require('../src/products/trial');
  let seenRoute = null;
  const product = {
    id: 'route_reader',
    path: '/x402/fake',
    routes: [{ method: 'GET', path: '/x402/fake' }],
    priceUsd: '0.01',
    cachedAvailability: async () => ({ available: true }),
    validate: (ctx) => ({ kind: ctx.route.path.endsWith('/blocks') ? 'blocks' : null }),
    handler: async (ctx) => { seenRoute = ctx.route; return { status: 200, bodyObj: { ok: true } }; },
  };
  const store = { consumeTrial: () => ({ allowed: true, remaining: 1, used: 1 }) };
  const route = createTrialRoute({ product, cfg, gatewayStore: store, limitPerDay: 2, now: () => 0 });
  const out = await route.handler({ method: 'GET', headers: {}, query: new URLSearchParams(), json: null });
  assert.equal(out.status, 200, 'validate() must not crash for want of ctx.route');
  // it mirrors the PAID path, not the /trial suffix — that is what validate reads
  assert.equal(seenRoute.path, '/x402/fake');
});

test('a crash inside validate() is a 500 with no internals, never a 400 blaming the caller', async () => {
  const { createTrialRoute } = require('../src/products/trial');
  const product = {
    id: 'crasher',
    path: '/x402/fake',
    routes: [{ method: 'POST', path: '/x402/fake' }],
    priceUsd: '0.01',
    cachedAvailability: async () => ({ available: true }),
    validate: () => { throw new TypeError("Cannot read properties of undefined (reading 'path')"); },
    handler: async () => ({ status: 200, bodyObj: { ok: true } }),
  };
  let spent = 0;
  const store = { consumeTrial: () => { spent += 1; return { allowed: true, remaining: 1, used: 1 }; } };
  const route = createTrialRoute({ product, cfg, gatewayStore: store, limitPerDay: 2, now: () => 0 });
  const out = await route.handler({ method: 'POST', headers: {}, query: new URLSearchParams(), json: {} });
  assert.equal(out.status, 500);
  assert.equal(out.bodyObj.error, 'trial_error');
  assert.equal(out.bodyObj.quota_spent, false);
  assert.equal(spent, 0, 'our bug must not burn the caller\'s quota');
  assert.doesNotMatch(JSON.stringify(out.bodyObj), /Cannot read properties|TypeError/,
    'a raw JS message must never reach the caller');
});

// The paid path's rule applies inside the trial too: a refusal must teach.
test('a trial validation 400 carries the input schema and the paid price', async () => {
  const { createTrialRoute } = require('../src/products/trial');
  const { ProductError } = require('../src/products/errors');
  const product = {
    id: 'strict',
    path: '/x402/fake',
    routes: [{ method: 'POST', path: '/x402/fake' }],
    priceUsd: '0.0075',
    trialLimitPerDay: 5,
    outputSchema: { input: { type: 'http', method: 'POST', bodyType: 'json', bodyFields: { input: { type: 'string', required: true } } } },
    cachedAvailability: async () => ({ available: true }),
    validate: () => { throw new ProductError('input is required', { status: 400, body: { error: 'invalid_request', detail: 'input is required' } }); },
    handler: async () => ({ status: 200, bodyObj: { ok: true } }),
  };
  const store = { consumeTrial: () => ({ allowed: true, remaining: 1, used: 1 }) };
  const route = createTrialRoute({ product, cfg, gatewayStore: store, limitPerDay: 5, now: () => 0 });
  const out = await route.handler({ method: 'POST', headers: {}, query: new URLSearchParams(), json: { text: 'wrong key' } });
  assert.equal(out.status, 400);
  assert.equal(out.bodyObj.detail, 'input is required', 'the product\'s own diagnosis survives');
  assert.ok(out.bodyObj.input_schema.bodyFields.input, 'the schema it should have written to');
  assert.equal(out.bodyObj.price_usd, '0.0075');
  assert.equal(out.bodyObj.quota_spent, false);
  // pointless inside a trial: do not advertise the trial to someone using it
  assert.equal(out.bodyObj.free_trial, undefined);
});

// preSettle is not a money hook in this codebase — it is the PIN hook, and its
// return value is state the handler then reads (bulk_chain, chain_balances,
// notary and lease all read ctx.pinned.*). The trial skipped it "because no
// money moves", so those handlers dereferenced an absent ctx.pinned: a live 502
// on /x402/chain/export/trial, with the quota spent on the failure.
test('a trial runs preSettle and hands its pin to the handler', async () => {
  const { createTrialRoute } = require('../src/products/trial');
  let seenHead = null;
  const product = {
    id: 'pinner',
    path: '/x402/fake',
    routes: [{ method: 'GET', path: '/x402/fake' }],
    priceUsd: '0.01',
    cachedAvailability: async () => ({ available: true }),
    validate: () => ({ from: 10 }),
    preSettle: async (ctx) => ({ head: 500 + ctx.params.from }),
    handler: async (ctx) => { seenHead = ctx.pinned.head; return { status: 200, bodyObj: { ok: true } }; },
  };
  const store = { consumeTrial: () => ({ allowed: true, remaining: 1, used: 1 }) };
  const route = createTrialRoute({ product, cfg, gatewayStore: store, limitPerDay: 2, now: () => 0 });
  const out = await route.handler({ method: 'GET', headers: {}, query: new URLSearchParams(), json: null });
  assert.equal(out.status, 200);
  assert.equal(seenHead, 510, 'preSettle ran, saw validated params, and its pin reached the handler');
});

test('a preSettle refusal is a 503 that does NOT burn the quota', async () => {
  const { createTrialRoute } = require('../src/products/trial');
  const { ProductUnavailable } = require('../src/products/errors');
  let spent = 0;
  const product = {
    id: 'not_ready',
    path: '/x402/fake',
    routes: [{ method: 'POST', path: '/x402/fake' }],
    priceUsd: '0.01',
    cachedAvailability: async () => ({ available: true }),
    validate: () => ({}),
    preSettle: async () => { throw new ProductUnavailable('pool_empty', 'no worker online'); },
    handler: async () => ({ status: 200, bodyObj: { ok: true } }),
  };
  const store = { consumeTrial: () => { spent += 1; return { allowed: true, remaining: 1, used: 1 }; } };
  const route = createTrialRoute({ product, cfg, gatewayStore: store, limitPerDay: 2, now: () => 0 });
  const out = await route.handler({ method: 'POST', headers: {}, query: new URLSearchParams(), json: {} });
  assert.equal(out.status, 503);
  assert.equal(out.bodyObj.quota_spent, false);
  assert.equal(spent, 0, 'a readiness refusal costs the caller nothing');
});

test('a trial route hands validated params to the product exactly as the paid path does', async () => {
  const { createTrialRoute } = require('../src/products/trial');
  let seen = null;
  const product = {
    id: 'fake_product',
    path: '/x402/fake',
    routes: [{ method: 'POST', path: '/x402/fake' }],
    priceUsd: '0.01',
    cachedAvailability: async () => ({ available: true }),
    validate: () => ({ n: 8, label: 'x' }),
    handler: async (ctx) => { seen = ctx; return { status: 200, bodyObj: { ok: true } }; },
  };
  const store = { consumeTrial: () => ({ allowed: true, remaining: 1, used: 1 }) };
  const route = createTrialRoute({ product, cfg, gatewayStore: store, limitPerDay: 2, now: () => 0 });
  await route.handler({ json: {}, headers: {}, ip: '203.0.113.9' });

  assert.ok(seen, 'the handler must run');
  assert.deepEqual(seen.params, { n: 8, label: 'x' }, 'ctx.params is where every product reads its input');
  assert.equal(seen.n, 8, 'the flat spread stays for backward compatibility');
  assert.equal(seen.trial, true);
});

test('the crawler fix names the actual cause, not a generic one', async () => {
  // robots.txt exclusion only: telling this owner to check their WAF would be
  // advice about a system that is not doing anything.
  const robotsOnly = fixtureFetch(['User-agent: GPTBot', 'User-agent: ClaudeBot', 'Disallow: /'].join('\n'));
  let p = createGeoAuditProduct({ cfg, fetchImpl: robotsOnly.impl, lookup: publicLookup });
  let fix = (await audit(p)).fixes.find((f) => f.id === 'ai_crawler_access').fix;
  assert.match(fix, /your own robots\.txt/);
  assert.doesNotMatch(fix, /WAF/, 'no edge rule is involved when the exclusion is in robots.txt');

  // Edge block against an agent robots.txt permits: that IS a WAF problem.
  const edge = fixtureFetch('User-agent: *\nDisallow:\n', {
    homeStatusFor: (ua) => (ua.includes('ClaudeBot') ? 403 : 200),
  });
  p = createGeoAuditProduct({ cfg, fetchImpl: edge.impl, lookup: publicLookup });
  const f = (await audit(p)).fixes.find((x) => x.id === 'ai_crawler_access');
  assert.match(f.fix, /WAF/);
  assert.match(f.fix, /ClaudeBot/, 'name the agent that is actually being refused');
  assert.equal(f.contradiction, true);
});
