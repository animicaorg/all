'use strict';
/**
 * Discovery-surface tests: the catalog, the landing page, the OpenAPI
 * document, the aggregate stats endpoint and the descriptive metadata in
 * every 402.
 *
 * The invariant behind all of them is the same one: THESE SURFACES ARE
 * GENERATED FROM THE LIVE REGISTRY. So the tests do not assert "$0.01"
 * anywhere — they change a price in the config and assert that every surface
 * moved with it. A hardcoded copy of a price, a path or an availability flag
 * fails here.
 *
 * The OpenAPI document was additionally validated against the official
 * OpenAPI 3.1 schema with `openapi-spec-validator` 0.9.0 (2026-08-15,
 * `/root/animica/.venv/bin/python -c "from openapi_spec_validator import
 * validate; import json; validate(json.load(open('openapi.json')))"` ->
 * valid). That tool is not a dependency of this app, so the structural
 * checks below are what CI enforces: every $ref resolves, every operation
 * has a unique id, every tag is declared, and the 402 challenge is a
 * documented response on every paid operation.
 */

const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');

const cfgMod = require('../src/config');
const protocol = require('../src/protocol');
const { createStore } = require('../src/store');
const { createSettlementStats, pathOfResource } = require('../src/discovery/stats');
const { buildTestGateway, request } = require('./gateway-helpers');

const HTML_ACCEPT = 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8';

/* ------------------------------------------------------------- catalog -- */

test('catalog: spec §1 identity + every product generated from the registry', async () => {
  const t = await buildTestGateway();
  try {
    const cat = (await request(t.baseUrl, '/x402')).json;
    const base = t.gw.cfg.resourceBaseUrl;

    assert.equal(cat.name, 'Animica x402');
    assert.equal(cat.provider, 'Animica');
    assert.equal(cat.homepage, 'https://animica.org');
    assert.equal(cat.gateway, `${base}/x402`);
    assert.equal(cat.payment_protocol, 'x402');
    assert.equal(cat.network, 'base');
    assert.equal(cat.chain_id, 8453);
    assert.equal(cat.asset, 'USDC');
    assert.equal(cat.asset_address, t.gw.cfg.usdcAsset);
    assert.equal(cat.asset_decimals, 6);
    assert.deepEqual(cat.discovery, {
      catalog: `${base}/x402`,
      well_known: `${base}/.well-known/x402`,
      openapi: `${base}/x402/openapi.json`,
      stats: `${base}/x402/stats`,
    });

    // Every listed product matches its registry object, field for field.
    const listable = t.gw.registry.products.filter((p) => p.enabled || p.listedEvenWhenUnavailable);
    assert.equal(cat.products.length, listable.length);
    for (const product of listable) {
      const entry = cat.products.find((e) => e.id === product.id);
      assert.ok(entry, `${product.id} missing from the catalog`);
      assert.equal(entry.name, product.title);
      assert.equal(entry.path, product.path);
      assert.equal(entry.method, product.routes[0].method);
      assert.equal(entry.url, `${base}${product.path}`);
      assert.equal(entry.documentation, `${base}/x402#product-${product.id}`);
      assert.equal(entry.price, product.priceUsd);
      assert.equal(entry.price_atomic, cfgMod.usdToUsdcAtomic(product.priceUsd));
      assert.equal(entry.currency, 'USDC');
      assert.equal(entry.description, product.description);
      assert.deepEqual(entry.endpoints, product.routes.map((r) => `${r.method} ${r.path}`));
      const live = product.enabled ? await product.availability() : { available: false };
      assert.equal(entry.available, Boolean(live.available), `${product.id} availability drifted`);
      if (!live.available) assert.equal(entry.unavailable_reason, live.reason);
    }
  } finally {
    await t.close();
  }
});

test('catalog: a price change moves every surface (nothing is hardcoded)', async () => {
  const t = await buildTestGateway({ overrides: { qrngPriceUsd: '0.037' } });
  try {
    const cat = (await request(t.baseUrl, '/x402')).json;
    const qrng = cat.products.find((p) => p.id === 'qrng');
    assert.equal(qrng.price, '0.037');
    assert.equal(qrng.price_atomic, '37000');

    // ...the landing page (hero lead and the product's own card),
    const html = (await request(t.baseUrl, '/x402', { headers: { accept: HTML_ACCEPT } })).text;
    assert.match(html, /Verifiable quantum randomness for \$0\.037 per request/);
    const card = html.slice(html.indexOf('id="product-qrng"'), html.indexOf('id="product-random_int"'));
    assert.match(card, /\$0\.037 USDC per request/);
    assert.doesNotMatch(card, /\$0\.01 USDC per request/);

    // ...the OpenAPI document,
    const doc = (await request(t.baseUrl, '/x402/openapi.json')).json;
    assert.equal(doc.paths['/x402/qrng/draw'].get['x-payment-info'].price, '0.037');
    assert.equal(doc.paths['/x402/qrng/draw'].get['x-payment-info'].amount_atomic, '37000');

    // ...and the 402 itself.
    const res = await request(t.baseUrl, '/x402/qrng/draw');
    const required = protocol.decodeHeader(res.headers.get('payment-required'));
    assert.equal(required.accepts[0].amount, '37000');
    assert.equal(required.extensions.animica.price, '0.037');
  } finally {
    await t.close();
  }
});

test('discovery: content negotiation — browsers get HTML, agents get the catalog', async () => {
  const t = await buildTestGateway();
  try {
    const html = await request(t.baseUrl, '/x402', { headers: { accept: HTML_ACCEPT } });
    assert.equal(html.status, 200);
    assert.match(html.headers.get('content-type'), /^text\/html/);
    assert.match(html.text, /^<!doctype html>/);

    for (const accept of ['application/json', '*/*', 'application/json, text/plain, */*', '']) {
      const res = await request(t.baseUrl, '/x402', { headers: accept ? { accept } : {} });
      assert.match(res.headers.get('content-type'), /application\/json/, `accept: ${accept}`);
      assert.equal(res.json.name, 'Animica x402');
    }
    // A client that says it prefers JSON over HTML gets JSON even when it
    // lists both.
    const mixed = await request(t.baseUrl, '/x402', { headers: { accept: 'text/html;q=0.3, application/json;q=0.9' } });
    assert.match(mixed.headers.get('content-type'), /application\/json/);

    // ?format= overrides negotiation in both directions (monitoring scripts).
    const forcedJson = await request(t.baseUrl, '/x402?format=json', { headers: { accept: HTML_ACCEPT } });
    assert.match(forcedJson.headers.get('content-type'), /application\/json/);
    const forcedHtml = await request(t.baseUrl, '/x402?format=html', { headers: { accept: 'application/json' } });
    assert.match(forcedHtml.headers.get('content-type'), /^text\/html/);

    // /.well-known/ is a machine location: it NEVER returns a web page.
    const wk = await request(t.baseUrl, '/.well-known/x402', { headers: { accept: HTML_ACCEPT } });
    assert.match(wk.headers.get('content-type'), /application\/json/);
    assert.deepEqual(wk.json.products.map((p) => p.id), (await request(t.baseUrl, '/x402')).json.products.map((p) => p.id));
  } finally {
    await t.close();
  }
});

/* --------------------------------------------------- production / echo -- */

test('production: the development echo is invisible on every surface', async () => {
  const t = await buildTestGateway({ overrides: { env: 'production', echoEnabled: false } });
  try {
    const cat = (await request(t.baseUrl, '/x402')).json;
    assert.ok(!cat.products.some((p) => p.id === 'echo'));

    const html = (await request(t.baseUrl, '/x402', { headers: { accept: HTML_ACCEPT } })).text;
    assert.doesNotMatch(html, /echo/i);

    const doc = (await request(t.baseUrl, '/x402/openapi.json')).json;
    assert.ok(!Object.keys(doc.paths).some((p) => /echo/.test(p)));
    assert.doesNotMatch(JSON.stringify(doc), /echo/i);

    // 410, not 404: the route was listed with an indexer before the real
    // products existed, so monitors still probe it and must be forwarded.
    assert.equal((await request(t.baseUrl, '/x402/paid/echo')).status, 410);
  } finally {
    await t.close();
  }
});

test('development: echo is catalogued but flagged, and still never advertised', async () => {
  const t = await buildTestGateway(); // dev default: echo enabled
  try {
    const cat = (await request(t.baseUrl, '/x402')).json;
    const echo = cat.products.find((p) => p.id === 'echo');
    assert.ok(echo, 'dev catalog still lists echo for smoke tests');
    assert.equal(echo.development_only, true);

    // ...but the human page and the OpenAPI document — the surfaces a buyer
    // or an indexer reads — never mention it, in any environment.
    const html = (await request(t.baseUrl, '/x402', { headers: { accept: HTML_ACCEPT } })).text;
    assert.doesNotMatch(html, /echo/i);
    const doc = (await request(t.baseUrl, '/x402/openapi.json')).json;
    assert.ok(!Object.keys(doc.paths).some((p) => /echo/.test(p)));
  } finally {
    await t.close();
  }
});

/* -------------------------------------------------------- landing page -- */

test('landing: structure, JSON-LD, canonical and live availability', async () => {
  const t = await buildTestGateway();
  try {
    const res = await request(t.baseUrl, '/x402', { headers: { accept: HTML_ACCEPT } });
    const html = res.text;
    const base = t.gw.cfg.resourceBaseUrl;

    // self-contained: no executable script, no external asset of any kind
    assert.doesNotMatch(html, /<script(?![^>]*type="application\/ld\+json")/);
    assert.doesNotMatch(html, /<(script|img|iframe|video|audio|object|embed)[^>]+src=/i);
    assert.doesNotMatch(html, /<link[^>]+rel="(stylesheet|preload|prefetch)"/i);
    assert.doesNotMatch(html, /@import|url\(https?:/i);
    assert.match(res.headers.get('content-security-policy') || '', /default-src 'none'/);

    // metadata (spec §9)
    assert.match(html, new RegExp(`<link rel="canonical" href="${base}/x402">`));
    assert.match(html, /<meta name="description" content="[^"]{80,}">/);
    assert.match(html, /<meta name="robots" content="index,follow">/);
    assert.match(html, /<title>Animica x402 — pay-per-request APIs for agents<\/title>/);

    // JSON-LD: both graphs present and parseable
    const blocks = [...html.matchAll(/<script type="application\/ld\+json">([\s\S]*?)<\/script>/g)]
      .map((m) => JSON.parse(m[1]));
    assert.equal(blocks.length, 2);
    const webApi = blocks.find((b) => b['@type'] === 'WebAPI');
    const faq = blocks.find((b) => b['@type'] === 'FAQPage');
    assert.ok(webApi && faq);
    assert.equal(webApi.url, `${base}/x402`);
    assert.equal(webApi.provider.name, 'Animica');
    // one offer per sellable product, priced from the registry
    const cat = (await request(t.baseUrl, '/x402')).json;
    const sellable = cat.products.filter((p) => !p.development_only);
    assert.equal(webApi.offers.length, sellable.length);
    for (const p of sellable) {
      const offer = webApi.offers.find((o) => o.name === p.name);
      assert.ok(offer, `${p.id} missing from JSON-LD offers`);
      assert.equal(offer.priceSpecification.price, p.price);
      assert.equal(offer.availability, p.available ? 'https://schema.org/InStock' : 'https://schema.org/OutOfStock');
    }
    assert.ok(faq.mainEntity.length >= 5);
    for (const q of faq.mainEntity) {
      assert.equal(q['@type'], 'Question');
      assert.ok(q.acceptedAnswer.text.length > 40);
    }

    // hero + product sections, in the spec's order
    assert.match(html, /<h1>Pay-per-request APIs for autonomous agents<\/h1>/);
    const iQrng = html.indexOf('id="product-qrng"');
    const iChain = html.indexOf('id="product-bulk_chain"');
    const iInf = html.indexOf('id="product-priority_inference"');
    assert.ok(iQrng > 0 && iQrng < iChain && iChain < iInf, 'randomness -> chain data -> inference');

    // every sellable product has its own anchored section = the documentation
    // URL published in the catalog and in each 402
    for (const p of sellable) {
      assert.ok(html.includes(`id="product-${p.id}"`), `${p.id} has no section`);
    }
  } finally {
    await t.close();
  }
});

test('landing: honest about the entropy source and about unavailability', async () => {
  const t = await buildTestGateway();
  try {
    const html = (await request(t.baseUrl, '/x402', { headers: { accept: HTML_ACCEPT } })).text;

    // The hero states the CURRENT truth next to the lead claim.
    assert.match(html, /software CSPRNG fallback/);
    assert.match(html, /software-fallback/);
    assert.match(html, /No hardware QRNG is connected and hardware attestation is not live/);
    assert.match(html, /is_quantum: false/);
    assert.match(html, /attested: false/);
    // ...and never the opposite.
    assert.doesNotMatch(html, /quantum-attested|hardware quantum|quantum-grade|true random(ness)? from quantum/i);
    assert.doesNotMatch(html, /hardware[- ]attested/i);

    // Priority inference is disabled by default: the page says so, in the
    // spec's words, and does not present it as buyable.
    assert.match(html, /Available when network serving capacity permits/);
    assert.match(html, /Not available right now/);
    assert.match(html, /unavailable — priority_inference_disabled/);
  } finally {
    await t.close();
  }
});

test('landing: an unhealthy backend flips the page to unavailable', async () => {
  const t = await buildTestGateway({
    handlers: {
      'chain.getHead': () => ({ height: 100, hash: '0x' + '00'.repeat(32) }),
      // sick source: the RPC answers 200 with health.passed false
      'rand.quantumRandomBytes': () => ({
        bytes_hex: 'aa'.repeat(8), n: 8,
        source: { name: 'software-fallback', is_hardware: false, is_quantum: false },
        health: { passed: false, min_entropy_per_byte: 1.2 },
      }),
    },
  });
  try {
    const html = (await request(t.baseUrl, '/x402', { headers: { accept: HTML_ACCEPT } })).text;
    assert.match(html, /unavailable — qrng_entropy_health_failed/);
    const cat = (await request(t.baseUrl, '/x402')).json;
    assert.equal(cat.products.find((p) => p.id === 'qrng').available, false);
  } finally {
    await t.close();
  }
});

/* ------------------------------------------------------------- openapi -- */

/** Collect every $ref in the document. */
function refsOf(node, out = []) {
  if (Array.isArray(node)) node.forEach((n) => refsOf(n, out));
  else if (node && typeof node === 'object') {
    for (const [k, v] of Object.entries(node)) {
      if (k === '$ref' && typeof v === 'string') out.push(v);
      else refsOf(v, out);
    }
  }
  return out;
}

function resolveRef(doc, ref) {
  assert.ok(ref.startsWith('#/'), `only local refs are allowed: ${ref}`);
  let cur = doc;
  for (const part of ref.slice(2).split('/')) {
    cur = cur[part.replace(/~1/g, '/').replace(/~0/g, '~')];
    if (cur === undefined) return undefined;
  }
  return cur;
}

test('openapi: 3.1 document generated from the registry, every ref resolving', async () => {
  const t = await buildTestGateway();
  try {
    const res = await request(t.baseUrl, '/x402/openapi.json');
    assert.equal(res.status, 200);
    assert.match(res.headers.get('content-type'), /application\/json/);
    const doc = res.json;

    assert.match(doc.openapi, /^3\.1\.\d+$/);
    assert.ok(doc.info.title && doc.info.version && doc.info.description);
    assert.equal(doc.info.license.identifier, 'Apache-2.0');
    assert.deepEqual(doc.servers, [{ url: t.gw.cfg.resourceBaseUrl, description: 'Animica x402 gateway' }]);
    assert.equal(doc['x-payment-protocol'], 'x402');
    assert.equal(doc['x-x402'].chain_id, 8453);
    assert.equal(doc['x-x402'].asset, 'USDC');

    // No fake authentication: the payment payload is the only credential.
    assert.equal(doc.components.securitySchemes, undefined);
    assert.equal(doc.security, undefined);
    assert.match(doc.info.description, /NO API-key authentication/);

    // every $ref resolves
    for (const ref of new Set(refsOf(doc))) {
      assert.notEqual(resolveRef(doc, ref), undefined, `unresolved ${ref}`);
    }

    // every product route in the registry is documented, once, with unique ids
    const ids = new Set();
    const declaredTags = new Set(doc.tags.map((tag) => tag.name));
    for (const [p, item] of Object.entries(doc.paths)) {
      assert.ok(p.startsWith('/'), p);
      for (const [method, op] of Object.entries(item)) {
        assert.ok(['get', 'post', 'put', 'delete'].includes(method));
        assert.ok(op.operationId, `${method} ${p} has no operationId`);
        assert.ok(!ids.has(op.operationId), `duplicate operationId ${op.operationId}`);
        ids.add(op.operationId);
        assert.ok(Object.keys(op.responses).length > 0);
        for (const tag of op.tags || []) assert.ok(declaredTags.has(tag), `undeclared tag ${tag}`);
      }
    }

    const paid = t.gw.registry.products.filter((p) => !p.devOnly && (p.enabled || p.listedEvenWhenUnavailable));
    const cat = (await request(t.baseUrl, '/x402')).json;
    for (const product of paid) {
      for (const route of product.routes) {
        const op = doc.paths[route.path] && doc.paths[route.path][route.method.toLowerCase()];
        assert.ok(op, `${route.method} ${route.path} is not documented`);
        // the 402 challenge is documented honestly, with its header
        assert.ok(op.responses['402'], `${route.path} does not document the 402 challenge`);
        assert.equal(op['x-payment-protocol'], 'x402');
        const info = op['x-payment-info'];
        const entry = cat.products.find((e) => e.id === product.id);
        assert.equal(info.product, product.id);
        assert.equal(info.price, entry.price);
        assert.equal(info.amount_atomic, entry.price_atomic);
        assert.equal(info.network, 'base');
        assert.equal(info.chain_id, 8453);
        assert.equal(info.asset, 'USDC');
        assert.equal(info.asset_address, t.gw.cfg.usdcAsset);
        assert.equal(info.pay_to, t.gw.cfg.basePayTo);
        assert.equal(info.available, entry.available);
        assert.equal(info.documentation, entry.documentation);
      }
    }

    // the 402 response component carries the header a client must read
    const challenge = doc.components.responses.PaymentRequired;
    assert.ok(challenge.headers['payment-required']);
    assert.match(challenge.description, /Sign them locally and retry/);

    // free routes are documented as free, and the free discovery surfaces
    // exist so a crawler can enumerate everything without paying
    const reveal = doc.paths['/x402/random/reveal/{commit_id}'];
    assert.ok(reveal, 'the free commit-reveal disclosure must be documented');
    assert.equal(reveal.get['x-payment-info'].price, '0');
    assert.ok(!reveal.get.responses['402'], 'a free route must not document a 402');
    for (const p of ['/x402', '/.well-known/x402', '/x402/openapi.json', '/x402/stats', '/x402/healthz']) {
      assert.ok(doc.paths[p], `${p} missing from the document`);
    }
  } finally {
    await t.close();
  }
});

test('openapi: examples are the REAL captured responses, not invented JSON', async () => {
  const t = await buildTestGateway();
  try {
    const doc = (await request(t.baseUrl, '/x402/openapi.json')).json;
    const { SAMPLES } = require('../src/discovery/samples');

    const qrngExample = doc.paths['/x402/qrng/draw'].get.responses['200']
      .content['application/json'].examples.captured.value;
    assert.deepEqual(qrngExample, SAMPLES.qrng.response);
    // the captured example is the shape the product ACTUALLY returns today
    const paid = await request(t.baseUrl, '/x402/qrng/draw');
    assert.equal(paid.status, 402); // unpaid, but the schema is what matters
    assert.deepEqual(
      Object.keys(qrngExample).sort(),
      ['attestation', 'bytes', 'encoding', 'health', 'product', 'randomness', 'source', 'verification']
    );
    // The mocked-facilitator settlement of the capture never leaked into the
    // document: no sample carries a payment block, and no fake Base tx hash
    // (0x000…0001-style) appears anywhere.
    for (const [id, sample] of Object.entries(SAMPLES)) {
      const body = JSON.stringify(sample.response);
      assert.equal(sample.response.payment, undefined, `${id} sample kept its payment block`);
      assert.doesNotMatch(body, /"settlement_tx"/, `${id} sample kept a settlement tx`);
    }
    // (No hash-shaped guard here: the bulk-chain capture legitimately
    // contains all-zero state roots, which is what the live chain returns.)

    const gate503 = doc.paths['/x402/v1/chat/completions'].post.responses['503']
      .content['application/json'].examples.captured.value;
    assert.equal(gate503.error, 'priority_inference_unavailable');
    assert.equal(gate503.serving_workers, 0);
  } finally {
    await t.close();
  }
});

/* ----------------------------------------------------------- 402 metadata */

test('402: every paid route carries descriptive metadata + a documentation URL', async () => {
  const t = await buildTestGateway();
  try {
    const base = t.gw.cfg.resourceBaseUrl;
    const probes = [
      ['/x402/qrng/draw', 'GET', 'qrng'],
      ['/x402/chain/export?from=1&to=2', 'GET', 'bulk_chain'],
      ['/x402/random/int', 'POST', 'random_int'],
      ['/x402/chain/balances', 'POST', 'chain_batch_balances'],
    ];
    for (const [p, method, id] of probes) {
      const res = await request(t.baseUrl, p, { method });
      assert.equal(res.status, 402, p);
      const required = protocol.decodeHeader(res.headers.get('payment-required'));
      const meta = required.extensions.animica;
      assert.ok(meta, `${p} has no descriptive metadata`);
      assert.equal(meta.product, id);
      assert.equal(meta.provider, 'Animica');
      assert.equal(meta.currency, 'USDC');
      assert.equal(meta.documentation, `${base}/x402#product-${id}`);
      assert.equal(meta.catalog, `${base}/.well-known/x402`);
      assert.equal(meta.openapi, `${base}/x402/openapi.json`);
      assert.ok(meta.name && meta.description);
      assert.equal(meta.content_type, t.gw.registry.products.find((x) => x.id === id).mimeType);

      // The descriptive copy of the terms is COPIED from the accepts entry
      // being offered, so it can never quote a different price.
      const accepted = required.accepts[0];
      assert.deepEqual(meta.terms, {
        scheme: accepted.scheme,
        network: accepted.network,
        chain_id: 8453,
        amount_atomic: accepted.amount,
        asset: accepted.asset,
        pay_to: accepted.payTo,
      });
      assert.equal(meta.price, t.gw.registry.products.find((x) => x.id === id).priceUsd);

      // The open-spec bazaar discovery extension is untouched by all this.
      assert.ok(required.extensions.bazaar.info.input, `${p} lost its bazaar schema`);
    }
  } finally {
    await t.close();
  }
});

test('402: descriptive metadata never becomes a mandatory protocol field', async () => {
  const t = await buildTestGateway();
  try {
    // A payment that accepts the terms verbatim (and knows nothing about the
    // extensions) still works: the extension block is not part of the match.
    const res = await request(t.baseUrl, '/x402/qrng/draw');
    const required = protocol.decodeHeader(res.headers.get('payment-required'));
    protocol.validatePaymentRequirements(required.accepts[0]);
    assert.equal(required.accepts[0].extra, undefined);

    const { paidRequest } = require('./gateway-helpers');
    const { paid } = await paidRequest(t.baseUrl, '/x402/qrng/draw');
    assert.equal(paid.status, 200);
    assert.equal(paid.json.product, 'qrng');
  } finally {
    await t.close();
  }
});

/* --------------------------------------------------------------- stats -- */

function tempLedger() {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'x402-stats-'));
  return path.join(dir, 'x402.db');
}

/** Settle `n` payments against `resource` at `settledAt` (unix seconds). */
function seedLedger(store, { resource, n = 1, settledAt }) {
  for (let i = 0; i < n; i++) {
    const paymentId = `pay_${Math.random().toString(36).slice(2)}`;
    store.claim({
      paymentId,
      authorizationHash: '0x' + Math.random().toString(16).slice(2).padEnd(64, '0'),
      payer: '0x' + 'ab'.repeat(20),
      asset: '0x' + '22'.repeat(20),
      network: 'eip155:8453',
      amount: 10000n,
      resource,
      expiresAt: 2_000_000_000,
    });
    store.markSettled(paymentId, { txHash: '0x' + 'cd'.repeat(32) });
    if (settledAt !== undefined) {
      store.db.prepare('UPDATE payments SET settled_at = ? WHERE payment_id = ?').run(settledAt, paymentId);
    }
  }
}

test('stats: aggregate counts from the settlement ledger, per product, no payers', async () => {
  const dbPath = tempLedger();
  const store = createStore(dbPath);
  // Relative to the real clock: the stats module uses Date.now() for its
  // 24 h window, so a fixed calendar date here would make the test's answer
  // depend on the day it runs.
  const nowSec = Math.floor(Date.now() / 1000);
  seedLedger(store, { resource: 'https://animica.dev/x402/qrng/draw', n: 3, settledAt: nowSec - 60 });
  seedLedger(store, { resource: 'https://animica.dev/x402/chain/export', n: 1, settledAt: nowSec - 60 });
  seedLedger(store, { resource: 'https://animica.dev/x402/qrng/draw', n: 2, settledAt: nowSec - 3 * 86_400 });
  // an unrecognised resource must be bucketed, never echoed
  seedLedger(store, { resource: 'https://evil.example/<script>alert(1)</script>', n: 1, settledAt: nowSec - 60 });
  store.close();

  const t = await buildTestGateway({ overrides: { settlementDbPath: dbPath } });
  try {
    const res = await request(t.baseUrl, '/x402/stats');
    assert.equal(res.status, 200);
    const s = res.json;
    assert.equal(s.available, true);
    assert.equal(s.name, 'Animica x402');
    assert.equal(s.network, 'base');
    assert.equal(s.chain_id, 8453);
    assert.equal(s.asset, 'USDC');
    assert.equal(s.settlements.settled_total, 7);
    assert.equal(s.settlements.paid_requests_served_total, 7);
    assert.equal(s.settlements.settled_24h, 5);
    assert.ok(s.settlements.first_settled_at < s.settlements.last_settled_at);

    const byId = Object.fromEntries(s.products.map((p) => [p.id, p]));
    assert.equal(byId.qrng.settled_total, 5);
    assert.equal(byId.qrng.settled_24h, 3);
    assert.equal(byId.bulk_chain.settled_total, 1);
    assert.equal(byId.other.settled_total, 1);

    // Aggregate ONLY: no payer address, no transaction hash, no
    // client-controlled resource string, and no per-payment field of any kind.
    const text = res.text;
    // The only hex string on this endpoint is the public USDC contract:
    // no payer address, no settlement transaction hash.
    const hexes = [...text.matchAll(/0x[0-9a-fA-F]{10,}/g)].map((m) => m[0]);
    assert.deepEqual(hexes, [t.gw.cfg.usdcAsset]);
    assert.doesNotMatch(text, /evil\.example|<script>/i, 'client-supplied resources are bucketed, never echoed');
    assert.deepEqual(Object.keys(s.settlements).sort(), [
      'first_settled_at', 'last_settled_at', 'paid_requests_served_total', 'settled_24h', 'settled_total',
    ]);
    for (const p of s.products) {
      assert.deepEqual(Object.keys(p).sort(), ['id', 'settled_24h', 'settled_total']);
    }
  } finally {
    await t.close();
    fs.rmSync(path.dirname(dbPath), { recursive: true, force: true });
  }
});

test('stats: an absent ledger reports unknown instead of a confident zero', async () => {
  const t = await buildTestGateway(); // helper points at a path that does not exist
  try {
    const s = (await request(t.baseUrl, '/x402/stats')).json;
    assert.equal(s.available, false);
    assert.equal(s.reason, 'settlement_store_empty');
    assert.equal(s.settlements, null);
    assert.deepEqual(s.products, []);
    // the identity facts are still published, so the endpoint stays useful
    assert.equal(s.network, 'base');
    assert.equal(s.asset, 'USDC');
  } finally {
    await t.close();
  }
});

test('stats: a remote facilitator says so rather than counting a local file', () => {
  const cfg = cfgMod.loadGatewayConfig({}, {
    facilitatorMode: 'remote',
    networkEvm: cfgMod.NETWORKS.BASE_MAINNET,
    usdcAsset: cfgMod.USDC_DEFAULTS[cfgMod.NETWORKS.BASE_MAINNET],
    resourceBaseUrl: 'https://animica.dev',
    settlementDbPath: '/dev/null',
  });
  const stats = createSettlementStats({ cfg, registry: { products: [] } });
  const s = stats.snapshot();
  assert.equal(s.available, false);
  assert.equal(s.reason, 'external_facilitator');
  assert.equal(s.settlements, null);
});

test('stats: resource paths are normalised before the product lookup', () => {
  assert.equal(pathOfResource('https://animica.dev/x402/qrng/draw?bytes=32'), '/x402/qrng/draw');
  assert.equal(pathOfResource('/x402/qrng/draw/'), '/x402/qrng/draw');
  assert.equal(pathOfResource(''), '');
  assert.equal(pathOfResource(null), '');
});

// A route we retired after it was already listed with an indexer must not
// answer a bare 404: uptime monitors publish that as "origin broken", and a
// cached agent config has no way to find the replacement. 410 + a pointer lets
// a machine re-target itself.
test('retired echo answers 410 with a forwarding pointer, not 404', async () => {
  const t = await buildTestGateway({ overrides: { env: 'production', echoEnabled: false } });
  try {
    const res = await request(t.baseUrl, '/x402/paid/echo', { method: 'POST' });
    assert.equal(res.status, 410, 'deliberately gone, not merely missing');
    assert.equal(res.json.error, 'gone');
    assert.equal(res.json.catalog, '/.well-known/x402');
    assert.ok(res.json.suggested, 'points at a live product');
  } finally {
    await t.close();
  }
});

/* ------------------------------------------- probe methods: HEAD, OPTIONS -- */

// Indexers and uptime monitors probe with HEAD before they ever send a GET.
// While HEAD fell through to the 404 branch, every POST-only product read as
// dead to a crawler that never followed up with a GET.
test('HEAD mirrors GET on paid routes: same status and headers, empty body', async () => {
  const t = await buildTestGateway();
  try {
    for (const p of ['/x402/qrng/draw', '/x402/random/int', '/x402/chain/balances']) {
      const head = await request(t.baseUrl, p, { method: 'HEAD' });
      const get = await request(t.baseUrl, p);
      assert.equal(head.status, 402, `${p}: HEAD must return the payment challenge`);
      assert.equal(head.status, get.status, `${p}: HEAD and GET must agree on status`);
      assert.ok(
        head.headers.get(protocol.HEADER_PAYMENT_REQUIRED),
        `${p}: the challenge header is the whole point of the probe`,
      );
      assert.equal(head.text, '', `${p}: HEAD carries no body`);
    }
  } finally {
    await t.close();
  }
});

test('HEAD works on the discovery surfaces too', async () => {
  const t = await buildTestGateway();
  try {
    for (const p of ['/x402', '/.well-known/x402', '/x402/openapi.json', '/x402/stats', '/x402/healthz']) {
      const res = await request(t.baseUrl, p, { method: 'HEAD' });
      assert.equal(res.status, 200, `${p}: HEAD must not 404`);
      assert.equal(res.text, '', `${p}: HEAD carries no body`);
    }
  } finally {
    await t.close();
  }
});

// A browser-hosted agent cannot read a 402 challenge at all without the
// preflight, because the challenge is header-carried.
test('OPTIONS advertises the real methods and exposes the payment headers', async () => {
  const t = await buildTestGateway();
  try {
    const post = await request(t.baseUrl, '/x402/random/int', { method: 'OPTIONS' });
    assert.equal(post.status, 204);
    const allow = post.headers.get('allow').split(', ');
    assert.ok(allow.includes('POST'), 'POST-only product must advertise POST');
    assert.ok(allow.includes('OPTIONS'));
    assert.equal(post.headers.get('access-control-allow-origin'), '*');
    assert.match(post.headers.get('access-control-allow-headers'), /payment-signature/);
    assert.match(post.headers.get('access-control-expose-headers'), /payment-required/);

    const get = await request(t.baseUrl, '/x402/qrng/draw', { method: 'OPTIONS' });
    assert.equal(get.status, 204);
    const getAllow = get.headers.get('allow').split(', ');
    assert.ok(getAllow.includes('GET') && getAllow.includes('HEAD'),
      'a GET product advertises GET and HEAD');

    // Unknown paths still 404 — OPTIONS must not invent surface area.
    assert.equal((await request(t.baseUrl, '/x402/nope', { method: 'OPTIONS' })).status, 404);
  } finally {
    await t.close();
  }
});

test('every response is readable cross-origin', async () => {
  const t = await buildTestGateway();
  try {
    for (const p of ['/x402', '/x402/qrng/draw']) {
      const res = await request(t.baseUrl, p);
      assert.equal(res.headers.get('access-control-allow-origin'), '*', `${p}`);
      assert.match(res.headers.get('access-control-expose-headers'), /payment-required/, `${p}`);
    }
  } finally {
    await t.close();
  }
});
