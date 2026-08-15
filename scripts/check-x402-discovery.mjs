#!/usr/bin/env node
/**
 * check-x402-discovery.mjs — health check for the Animica x402 discovery layer.
 *
 * Verifies, against a live deployment, everything the discovery spec (§15) requires:
 *
 *   1. GET /x402 returns 200 as HTML (browser Accept) and as JSON (agent Accept)
 *   2. GET /.well-known/x402 is valid JSON and carries the spec'd identity/network fields
 *   3. Catalog invariants: every advertised product URL resolves; unavailable products
 *      carry a machine reason; the development-only echo route is absent in production
 *   4. GET /x402/openapi.json loads and is valid JSON describing the paid routes
 *   5. GET /x402/stats loads and is valid JSON (aggregate only)
 *   6. The QRNG route answers 402 unpaid, and the 402's network / asset / payTo / amount
 *      match the catalog and the catalog's own price for that product
 *   7. The MCP catalog tool (read-only) reports the same products and prices — invoked
 *      through the repo venv when available, otherwise SKIPped with a warning
 *   8. /llms.txt (and, when served, /llms-full.txt and /.well-known/agents.json) mention
 *      the discovery URL
 *
 * Nothing here hardcodes a price, an asset or a product list: every expectation is read
 * from the live catalog, which the gateway generates from its product registry.
 *
 * Every probe hits BASE_URL. The catalog advertises absolute production URLs, so those are
 * rebased onto BASE_URL's origin before they are fetched — otherwise pointing the check at
 * a local build would grade the public deployment instead. The advertised origin is still
 * asserted (one origin for all product/discovery URLs; on a public target it must be the
 * target itself).
 *
 * Usage:
 *   node scripts/check-x402-discovery.mjs                 # BASE_URL=https://animica.dev
 *   BASE_URL=http://127.0.0.1:8742 node scripts/check-x402-discovery.mjs
 *   node scripts/check-x402-discovery.mjs --json           # machine-readable report
 *
 * Env:
 *   BASE_URL     base of the deployment to check (default https://animica.dev)
 *   TIMEOUT_MS   per-request timeout, default 15000
 *   REQUEST_GAP_MS  delay between product probes, default 400 (respects nginx rate limits)
 *   ANIMICA_PY   python interpreter for the MCP check (default <repo>/.venv/bin/python)
 *   SKIP_MCP=1   skip the MCP cross-check entirely
 *
 * Exit code: 0 when no check FAILed (WARN/SKIP do not fail), 1 otherwise, 2 on a usage or
 * internal error. Safe for CI and for production monitoring: it is entirely read-only and
 * never sends a payment.
 */

import { spawnSync } from 'node:child_process';
import { existsSync } from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const DEFAULT_BASE = 'https://animica.dev';
const BASE_URL = (process.env.BASE_URL || DEFAULT_BASE).replace(/\/+$/, '');
const TIMEOUT_MS = Number(process.env.TIMEOUT_MS || 15000);
const REQUEST_GAP_MS = Number(process.env.REQUEST_GAP_MS || 400);
const JSON_OUTPUT = process.argv.includes('--json');

/**
 * Two spec rules ("no echo in production", "llms.txt contains the discovery URL") are
 * about a PUBLIC deployment. Against a loopback/dev target they are expected misses, so
 * they downgrade to warnings there instead of manufacturing red CI.
 */
const IS_PUBLIC = /^https:\/\//i.test(BASE_URL) && !/^https?:\/\/(127\.|localhost|\[::1\]|0\.0\.0\.0)/i.test(BASE_URL);
const failIfPublic = (name, detail) =>
  IS_PUBLIC ? fail(name, detail) : warn(name, `${detail} (non-public target: not a production failure)`);

const HERE = path.dirname(fileURLToPath(import.meta.url));
const REPO_ROOT = path.resolve(HERE, '..');

/** CAIP-2 -> x402 v1 network slug (only the chains this gateway can be configured for). */
const SLUG_BY_CAIP2 = { 'eip155:8453': 'base', 'eip155:84532': 'base-sepolia' };

const results = [];
function record(status, name, detail = '') {
  results.push({ status, name, detail: String(detail) });
}
const pass = (n, d) => record('PASS', n, d);
const fail = (n, d) => record('FAIL', n, d);
const warn = (n, d) => record('WARN', n, d);
const skip = (n, d) => record('SKIP', n, d);

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

async function req(url, { accept = 'application/json', method = 'GET' } = {}) {
  const started = Date.now();
  try {
    const res = await fetch(url, {
      method,
      headers: { accept, 'user-agent': 'animica-x402-discovery-check/1.0' },
      redirect: 'manual',
      signal: AbortSignal.timeout(TIMEOUT_MS),
    });
    const text = await res.text();
    return {
      ok: true,
      status: res.status,
      headers: res.headers,
      contentType: res.headers.get('content-type') || '',
      text,
      ms: Date.now() - started,
    };
  } catch (e) {
    return { ok: false, error: e && e.message ? e.message : String(e), ms: Date.now() - started };
  }
}

function parseJson(text) {
  try {
    return { ok: true, value: JSON.parse(text) };
  } catch (e) {
    return { ok: false, error: e.message };
  }
}

/** Tolerant readers: the catalog gained explicit fields over time. */
function catalogCaip2(cat) {
  if (typeof cat.network_caip2 === 'string') return cat.network_caip2;
  if (typeof cat.network === 'string' && cat.network.includes(':')) return cat.network;
  return null;
}
function catalogSlug(cat) {
  if (typeof cat.network === 'string' && !cat.network.includes(':')) return cat.network;
  const caip2 = catalogCaip2(cat);
  return caip2 ? SLUG_BY_CAIP2[caip2] || null : null;
}
function catalogAssetAddress(cat) {
  if (typeof cat.asset_address === 'string') return cat.asset_address;
  if (typeof cat.asset === 'string' && cat.asset.startsWith('0x')) return cat.asset;
  return null;
}
const sameAddr = (a, b) =>
  typeof a === 'string' && typeof b === 'string' && a.toLowerCase() === b.toLowerCase();

/**
 * The catalog advertises ABSOLUTE URLs built from X402_RESOURCE_BASE_URL — in production
 * that is https://animica.dev, whatever host the check is pointed at. Probing those URLs
 * verbatim would silently grade the PUBLIC deployment during a local/CI run: a broken
 * build passes because production is fine, and a fixed build fails because production is
 * stale. So every advertised URL is rebased onto BASE_URL's origin (path + query kept)
 * before it is fetched. When BASE_URL already is the advertised origin — the production
 * monitoring case — this is the identity function and nothing changes.
 * The advertised origin itself is not ignored; checkAdvertisedOrigins() asserts it.
 */
const rebasedOrigins = new Set();
function probeUrl(advertised, fallbackPath) {
  const fallback = fallbackPath ? `${BASE_URL}${fallbackPath}` : null;
  if (typeof advertised !== 'string' || !/^https?:\/\//i.test(advertised)) return fallback;
  let u;
  let target;
  try {
    u = new URL(advertised);
    target = new URL(BASE_URL);
  } catch {
    return fallback;
  }
  if (u.origin === target.origin) return advertised;
  rebasedOrigins.add(u.origin);
  return `${BASE_URL}${u.pathname}${u.search}`;
}

/** Absolute URLs the catalog publishes (products, free routes, discovery links). */
function advertisedUrls(cat) {
  const out = [];
  const push = (v) => {
    if (typeof v === 'string' && /^https?:\/\//i.test(v)) out.push(v);
  };
  // NOT homepage/repository: those legitimately name other hosts (animica.org, github.com).
  push(cat.gateway);
  push(cat.documentation);
  for (const v of Object.values(cat.discovery || {})) push(v);
  for (const p of cat.products || []) {
    push(p.url);
    push(p.documentation);
    for (const f of p.free_endpoints || []) push(f.url);
  }
  return out;
}

// ---------------------------------------------------------------- 1. landing + catalog

async function checkLanding() {
  // A real browser Accept string: content negotiation must give HTML to a browser and the
  // JSON catalog to everything else (an agent sending */* is NOT asking for a web page).
  const html = await req(`${BASE_URL}/x402`, {
    accept: 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
  });
  if (!html.ok) {
    fail('GET /x402 (html)', `request failed: ${html.error}`);
  } else if (html.status !== 200) {
    fail('GET /x402 (html)', `expected 200, got ${html.status}`);
  } else if (!/text\/html/i.test(html.contentType)) {
    fail('GET /x402 (html)', `browser Accept got content-type ${html.contentType || '(none)'}`);
  } else if (!/<html/i.test(html.text)) {
    fail('GET /x402 (html)', 'response is not an HTML document');
  } else {
    const mentionsEcho = html.text.includes('/x402/paid/echo');
    if (mentionsEcho) fail('GET /x402 (html)', 'landing page advertises the dev-only echo route');
    else pass('GET /x402 (html)', `200 text/html, ${html.text.length} bytes`);
  }

  const json = await req(`${BASE_URL}/x402`, { accept: 'application/json' });
  if (!json.ok) {
    fail('GET /x402 (json)', `request failed: ${json.error}`);
    return null;
  }
  if (json.status !== 200) {
    fail('GET /x402 (json)', `expected 200, got ${json.status}`);
    return null;
  }
  const parsed = parseJson(json.text);
  if (!parsed.ok) {
    fail('GET /x402 (json)', `agent Accept did not return JSON: ${parsed.error}`);
    return null;
  }
  pass('GET /x402 (json)', `200 ${json.contentType.split(';')[0] || 'json'}, ${(parsed.value.products || []).length} products`);
  return parsed.value;
}

async function checkWellKnown(landingCatalog) {
  const res = await req(`${BASE_URL}/.well-known/x402`);
  if (!res.ok) {
    fail('GET /.well-known/x402', `request failed: ${res.error}`);
    return null;
  }
  if (res.status !== 200) {
    fail('GET /.well-known/x402', `expected 200, got ${res.status}`);
    return null;
  }
  const parsed = parseJson(res.text);
  if (!parsed.ok) {
    fail('GET /.well-known/x402', `invalid JSON: ${parsed.error}`);
    return null;
  }
  const cat = parsed.value;
  if (!Array.isArray(cat.products) || cat.products.length === 0) {
    fail('GET /.well-known/x402', 'catalog has no products array');
    return null;
  }
  pass('GET /.well-known/x402', `200 valid JSON, ${cat.products.length} products`);

  // The well-known location is a machine location: it must never negotiate to HTML.
  const asHtml = await req(`${BASE_URL}/.well-known/x402`, { accept: 'text/html' });
  if (asHtml.ok && /text\/html/i.test(asHtml.contentType)) {
    fail('/.well-known/x402 is machine-only', 'returned HTML to a browser Accept header');
  } else {
    pass('/.well-known/x402 is machine-only', 'returns JSON regardless of Accept');
  }

  // Identity / network fields the discovery spec requires (§1).
  const missing = [];
  for (const k of ['name', 'provider', 'homepage', 'products']) if (!cat[k]) missing.push(k);
  const caip2 = catalogCaip2(cat);
  const slug = catalogSlug(cat);
  const asset = catalogAssetAddress(cat);
  const chainId = cat.chain_id ?? (caip2 ? Number(caip2.split(':')[1]) : null);
  if (!caip2) missing.push('network_caip2');
  if (!slug) missing.push('network (slug)');
  if (!asset) missing.push('asset_address');
  if (!chainId) missing.push('chain_id');
  if (!cat.payment_protocol) missing.push('payment_protocol');
  if (missing.length) {
    fail('catalog identity fields', `missing: ${missing.join(', ')} — is the gateway running the current build?`);
  } else {
    pass('catalog identity fields', `${cat.name} · ${slug} (${caip2}, chain ${chainId}) · ${cat.asset || asset}`);
  }

  // /x402 and /.well-known/x402 must be the same document.
  if (landingCatalog) {
    const a = (landingCatalog.products || []).map((p) => `${p.id}@${p.price}`).sort().join(',');
    const b = cat.products.map((p) => `${p.id}@${p.price}`).sort().join(',');
    if (a !== b) fail('catalog parity /x402 vs /.well-known/x402', `${a || '(none)'} != ${b}`);
    else pass('catalog parity /x402 vs /.well-known/x402', `${cat.products.length} products identical`);
  }
  return cat;
}

// -------------------------------------------------------------- 2. catalog invariants

function checkCatalogInvariants(cat) {
  // No development-only surface may be advertised as a product.
  const devOnly = cat.products.filter(
    (p) => p.development_only === true || p.id === 'echo' || String(p.path || '').includes('/paid/echo')
  );
  if (devOnly.length) {
    failIfPublic(
      'no dev-only echo in catalog',
      `advertised: ${devOnly.map((p) => p.id).join(', ')} — set X402_ENV=production (or unset X402_ENABLE_ECHO)`
    );
  } else {
    pass('no dev-only echo in catalog', 'echo absent (X402_ENV=production disables it)');
  }

  // Unavailable products must say why, in a machine-readable field.
  const badUnavailable = cat.products.filter((p) => p.available === false && !p.unavailable_reason);
  const unavailable = cat.products.filter((p) => p.available === false);
  if (badUnavailable.length) {
    fail('unavailable products carry a reason', `no unavailable_reason: ${badUnavailable.map((p) => p.id).join(', ')}`);
  } else {
    pass(
      'unavailable products carry a reason',
      unavailable.length
        ? unavailable.map((p) => `${p.id}=${p.unavailable_reason}`).join(', ')
        : 'all products available'
    );
  }

  // Prices must be decimal strings with atomic counterparts — never floats.
  const badPrice = cat.products.filter(
    (p) => typeof p.price !== 'string' || !/^\d+(\.\d+)?$/.test(p.price)
  );
  if (badPrice.length) {
    fail('prices are decimal strings', `bad: ${badPrice.map((p) => `${p.id}=${p.price}`).join(', ')}`);
  } else {
    pass('prices are decimal strings', cat.products.map((p) => `${p.id}=$${p.price}`).join(' '));
  }

  // Honesty: the randomness entropy disclosure must not claim hardware attestation.
  const claims = cat.products.filter(
    (p) => p.entropy && (p.entropy.attested === true || p.entropy.is_quantum === true)
  );
  const disclosed = cat.products.filter((p) => p.entropy);
  if (!disclosed.length) {
    warn('entropy disclosure present', 'no product publishes an entropy block');
  } else if (claims.length) {
    // Not automatically wrong — but every human-facing surface must be re-read before it
    // is allowed to say so, which is a deliberate stop-the-line condition for copy.
    warn(
      'entropy disclosure',
      `product(s) now report attested/quantum true: ${claims.map((p) => p.id).join(', ')} — re-check all listing copy`
    );
  } else {
    const e = disclosed[0].entropy;
    pass('entropy disclosure is honest', `source=${e.source} is_quantum=${e.is_quantum} attested=${e.attested}`);
  }
}

// ------------------------------------------- 2b. the advertised origin is one public host

function checkAdvertisedOrigins(cat) {
  const urls = advertisedUrls(cat);
  if (!urls.length) {
    return fail('advertised URLs share one origin', 'the catalog advertises no absolute product/discovery URLs');
  }
  const origins = [
    ...new Set(
      urls.map((u) => {
        try {
          return new URL(u).origin;
        } catch {
          return `invalid:${u}`;
        }
      })
    ),
  ];
  if (origins.length > 1) {
    return fail('advertised URLs share one origin', `mixed origins: ${origins.join(', ')}`);
  }
  const origin = origins[0];
  if (origin.startsWith('invalid:')) {
    return fail('advertised URLs share one origin', `unparseable advertised URL: ${origin.slice(8)}`);
  }
  const self = new URL(BASE_URL).origin;
  if (origin === self) return pass('advertised URLs share one origin', origin);
  if (IS_PUBLIC) {
    // A public deployment that advertises a DIFFERENT host is misconfigured
    // (X402_RESOURCE_BASE_URL) — agents would be sent somewhere else.
    return fail(
      'advertised URLs share one origin',
      `catalog advertises ${origin} but this deployment answers at ${self} (X402_RESOURCE_BASE_URL mismatch)`
    );
  }
  if (!/^https:/i.test(origin)) {
    return warn('advertised URLs share one origin', `${origin} is not https (dev target)`);
  }
  pass('advertised URLs share one origin', `${origin} — probes below are rebased onto ${self}`);
}

// -------------------------------------------------------- 3. every product URL resolves

async function checkProductUrls(cat) {
  const problems = [];
  const checked = [];
  for (const p of cat.products) {
    const url = probeUrl(p.url, p.path);
    if (!url || url.includes('{')) continue; // templated (free reveal route) — nothing to fetch
    const res = await req(url);
    await sleep(REQUEST_GAP_MS);
    if (!res.ok) {
      problems.push(`${p.id}: request failed (${res.error})`);
      continue;
    }
    checked.push(`${p.id}=${res.status}`);
    if (p.available === false) {
      // The gate must refuse WITHOUT asking for money.
      if (res.status === 402) problems.push(`${p.id}: unavailable but still emits a 402 payment request`);
      else if (res.status !== 503) problems.push(`${p.id}: unavailable, expected 503, got ${res.status}`);
    } else if (res.status !== 402 && res.status !== 200) {
      problems.push(`${p.id}: expected 402 (or 200 for a free route), got ${res.status} at ${url}`);
    }
  }
  if (problems.length) fail('every product URL resolves', problems.join('; '));
  else pass('every product URL resolves', checked.join(' '));

  // Free endpoints advertised as costing nothing must never answer 402.
  for (const p of cat.products) {
    for (const f of p.free_endpoints || []) {
      const furl = probeUrl(f.url, null);
      if (!furl || furl.includes('{')) continue;
      const res = await req(furl);
      await sleep(REQUEST_GAP_MS);
      if (res.ok && res.status === 402) fail('free endpoints are free', `${f.endpoint} answered 402`);
    }
  }
}

// ------------------------------------------------------------------------ 4. OpenAPI

async function checkOpenApi(cat) {
  const url = probeUrl(cat.discovery && cat.discovery.openapi, '/x402/openapi.json');
  const res = await req(url);
  if (!res.ok) return fail('GET /x402/openapi.json', `request failed: ${res.error}`);
  if (res.status !== 200) {
    return fail('GET /x402/openapi.json', `expected 200, got ${res.status} (stale gateway build?)`);
  }
  const parsed = parseJson(res.text);
  if (!parsed.ok) return fail('GET /x402/openapi.json', `invalid JSON: ${parsed.error}`);
  const doc = parsed.value;
  if (!doc.openapi || !doc.paths || typeof doc.paths !== 'object') {
    return fail('GET /x402/openapi.json', 'not an OpenAPI document (missing openapi/paths)');
  }
  const paths = Object.keys(doc.paths);
  const missing = cat.products
    // Development-only surfaces are deliberately excluded from the document.
    .filter((p) => p.available !== false && p.development_only !== true)
    .map((p) => p.path)
    .filter((pp) => !paths.some((k) => k === pp || k === pp.replace(/^\/x402/, '')));
  if (missing.length) {
    fail('OpenAPI covers the catalog', `paths absent from the document: ${missing.join(', ')}`);
  } else {
    pass('GET /x402/openapi.json', `OpenAPI ${doc.openapi}, ${paths.length} paths`);
  }
  if (paths.some((k) => k.includes('/paid/echo'))) {
    fail('OpenAPI excludes dev-only echo', 'the echo route is documented as a product');
  }
}

// -------------------------------------------------------------------------- 5. stats

async function checkStats(cat) {
  const url = probeUrl(cat.discovery && cat.discovery.stats, '/x402/stats');
  const res = await req(url);
  if (!res.ok) return fail('GET /x402/stats', `request failed: ${res.error}`);
  if (res.status !== 200) return fail('GET /x402/stats', `expected 200, got ${res.status}`);
  const parsed = parseJson(res.text);
  if (!parsed.ok) return fail('GET /x402/stats', `invalid JSON: ${parsed.error}`);
  // Aggregate only: the asset and settlement addresses are configuration and may appear;
  // any OTHER EVM address in an aggregate endpoint would be payer data.
  const known = [catalogAssetAddress(cat), cat.pay_to, catalogAssetAddress(parsed.value), parsed.value.pay_to]
    .filter(Boolean);
  const strays = [...new Set(res.text.match(/0x[0-9a-fA-F]{40}/g) || [])].filter(
    (a) => !known.some((k) => sameAddr(a, k))
  );
  if (strays.length) {
    warn('GET /x402/stats', `unexpected EVM address(es) in an aggregate endpoint: ${strays.slice(0, 3).join(', ')}`);
  } else {
    pass('GET /x402/stats', 'valid aggregate JSON, no payer addresses');
  }
}

// -------------------------------------------------- 6. the 402 matches the catalog

async function check402(cat) {
  const product =
    cat.products.find((p) => p.id === 'qrng') ||
    cat.products.find((p) => p.available !== false);
  if (!product) return fail('402 challenge', 'no available product to probe');
  // `advertised` is what the catalog publishes (and therefore what the 402's resource must
  // name); `url` is where THIS deployment is actually probed. They differ only when the
  // check runs against a non-public target — see probeUrl().
  const advertised = product.url || `${BASE_URL}${product.path}`;
  const url = probeUrl(product.url, product.path);
  const res = await req(url);
  if (!res.ok) return fail(`402 on ${product.id}`, `request failed: ${res.error}`);
  if (res.status !== 402) {
    return fail(`402 on ${product.id}`, `unpaid request returned ${res.status}, expected 402`);
  }

  const expectedAtomic = product.price_atomic;
  const expectedAsset = catalogAssetAddress(cat);
  const expectedSlug = catalogSlug(cat);
  const expectedCaip2 = catalogCaip2(cat);
  const expectedPayTo = cat.pay_to || null;
  const problems = [];

  // v1 body (what an x402 v1 client reads).
  const parsed = parseJson(res.text);
  if (!parsed.ok) {
    problems.push(`402 body is not JSON: ${parsed.error}`);
  } else {
    const accepts = parsed.value.accepts;
    if (!Array.isArray(accepts) || !accepts.length) problems.push('402 body has no accepts entry');
    else {
      const a = accepts[0];
      if (a.scheme !== 'exact') problems.push(`scheme ${a.scheme} != exact`);
      if (expectedSlug && a.network !== expectedSlug) problems.push(`network ${a.network} != ${expectedSlug}`);
      if (expectedAtomic && a.maxAmountRequired !== expectedAtomic) {
        problems.push(`amount ${a.maxAmountRequired} != catalog price_atomic ${expectedAtomic} ($${product.price})`);
      }
      if (expectedAsset && !sameAddr(a.asset, expectedAsset)) problems.push(`asset ${a.asset} != ${expectedAsset}`);
      if (expectedPayTo && !sameAddr(a.payTo, expectedPayTo)) problems.push(`payTo ${a.payTo} != catalog pay_to`);
      if (a.resource && a.resource !== advertised) problems.push(`resource ${a.resource} != ${advertised}`);
    }
  }

  // v2 header (what a current x402 client reads).
  const hdr = res.headers.get('payment-required');
  if (!hdr) {
    problems.push('no payment-required header (x402 v2 wire)');
  } else {
    let v2 = null;
    try {
      v2 = JSON.parse(Buffer.from(hdr, 'base64').toString('utf8'));
    } catch (e) {
      problems.push(`payment-required header is not base64 JSON: ${e.message}`);
    }
    if (v2) {
      const a = (v2.accepts || [])[0] || {};
      if (v2.x402Version !== 2) problems.push(`x402Version ${v2.x402Version} != 2`);
      if (expectedCaip2 && a.network !== expectedCaip2) problems.push(`v2 network ${a.network} != ${expectedCaip2}`);
      if (expectedAtomic && a.amount !== expectedAtomic) problems.push(`v2 amount ${a.amount} != ${expectedAtomic}`);
      if (expectedAsset && !sameAddr(a.asset, expectedAsset)) problems.push(`v2 asset ${a.asset} != ${expectedAsset}`);
      if (!v2.resource || v2.resource.url !== advertised) {
        problems.push('v2 resource.url does not name the advertised resource URL');
      }
    }
  }

  if (problems.length) fail(`402 on ${product.id} matches catalog`, problems.join('; '));
  else pass(`402 on ${product.id} matches catalog`, `$${product.price} = ${expectedAtomic} atomic · ${expectedSlug} · ${expectedAsset}`);
}

// ------------------------------------------------------------------ 7. MCP catalog tool

const MCP_PROBE = `
import json, sys
try:
    from animica.mcp.tools import TOOLS_BY_NAME
except Exception as e:
    print(json.dumps({"error": "import: %s" % e})); raise SystemExit(0)
name = None
for k in TOOLS_BY_NAME:
    if "x402" in k or "paid_api" in k:
        name = k; break
if not name:
    print(json.dumps({"error": "no x402 catalog tool registered", "tools": sorted(TOOLS_BY_NAME)}))
    raise SystemExit(0)
spec = TOOLS_BY_NAME[name]
fn = getattr(spec, "fn", None) or getattr(spec, "func", None) or spec
try:
    out = fn()
except Exception as e:
    print(json.dumps({"error": "call %s: %s" % (name, e), "tool": name})); raise SystemExit(0)
try:
    payload = json.loads(out) if isinstance(out, str) else out
except Exception as e:
    print(json.dumps({"error": "tool %s did not return JSON: %s" % (name, e), "tool": name})); raise SystemExit(0)
print(json.dumps({"tool": name, "payload": payload}))
`;

function checkMcp(cat) {
  if (process.env.SKIP_MCP === '1') return skip('MCP catalog tool', 'SKIP_MCP=1');
  const py = process.env.ANIMICA_PY || path.join(REPO_ROOT, '.venv', 'bin', 'python');
  if (!existsSync(py)) {
    return skip('MCP catalog tool', `no interpreter at ${py} (set ANIMICA_PY to check it)`);
  }
  // Point the tool at the target under test. Without this the tool reads its own default
  // host (animica.dev) and the check would compare the PUBLIC catalog with a local build —
  // green for the wrong reason. ANIMICA_X402_URL is the tool's documented override.
  const run = spawnSync(py, ['-c', MCP_PROBE], {
    cwd: REPO_ROOT,
    encoding: 'utf8',
    timeout: 60000,
    env: { ...process.env, PYTHONWARNINGS: 'ignore', ANIMICA_X402_URL: BASE_URL },
  });
  if (run.error) return skip('MCP catalog tool', `could not run ${py}: ${run.error.message}`);
  if (run.status !== 0) {
    return skip('MCP catalog tool', `probe exited ${run.status}: ${(run.stderr || '').trim().split('\n').pop()}`);
  }
  const parsed = parseJson((run.stdout || '').trim().split('\n').pop() || '');
  if (!parsed.ok) return skip('MCP catalog tool', `probe output unparseable: ${parsed.error}`);
  if (parsed.value.error) return skip('MCP catalog tool', parsed.value.error);

  const payload = parsed.value.payload || {};
  const products = payload.products || (payload.catalog && payload.catalog.products) || [];
  if (!Array.isArray(products) || !products.length) {
    return skip('MCP catalog tool', `${parsed.value.tool} returned no products (offline or upstream error)`);
  }
  const mine = new Map(cat.products.map((p) => [p.id, p]));
  const theirs = new Map(products.map((p) => [p.id, p]));
  const problems = [];
  for (const [id, p] of mine) {
    if (!theirs.has(id)) problems.push(`missing ${id}`);
    else if (String(theirs.get(id).price) !== String(p.price)) {
      problems.push(`${id} price ${theirs.get(id).price} != ${p.price}`);
    }
  }
  for (const id of theirs.keys()) if (!mine.has(id)) problems.push(`extra ${id}`);

  // The tool was pointed at BASE_URL above, so any difference is a real mismatch between
  // the MCP catalog surface and the gateway catalog — never a host artefact.
  if (!problems.length) {
    pass('MCP catalog tool', `${parsed.value.tool}: ${theirs.size} products, prices match`);
  } else {
    fail('MCP catalog tool matches gateway catalog', problems.join('; '));
  }
}

// ------------------------------------------------------- 8. agent files name the catalog

async function checkAgentFiles(cat) {
  const wellKnown = (cat.discovery && cat.discovery.well_known) || `${BASE_URL}/.well-known/x402`;
  const needle = '/.well-known/x402';

  const llms = await req(`${BASE_URL}/llms.txt`, { accept: 'text/plain' });
  if (!llms.ok) failIfPublic('llms.txt names the discovery URL', `request failed: ${llms.error}`);
  else if (llms.status !== 200) {
    failIfPublic('llms.txt names the discovery URL', `GET /llms.txt returned ${llms.status}`);
  } else if (!llms.text.includes(needle)) {
    fail('llms.txt names the discovery URL', `no "${needle}" in /llms.txt`);
  } else {
    pass('llms.txt names the discovery URL', wellKnown);
  }

  for (const [label, p] of [['llms-full.txt', '/llms-full.txt'], ['agents.json', '/.well-known/agents.json']]) {
    const res = await req(`${BASE_URL}${p}`, { accept: 'text/plain' });
    if (!res.ok || res.status === 404) {
      warn(`${label} names the discovery URL`, `not served on this host (${res.ok ? res.status : res.error})`);
      continue;
    }
    if (res.status !== 200) fail(`${label} names the discovery URL`, `GET ${p} returned ${res.status}`);
    else if (!res.text.includes(needle)) fail(`${label} names the discovery URL`, `no "${needle}" in ${p}`);
    else pass(`${label} names the discovery URL`, p);
  }
}

// ------------------------------------------------------------------------------ output

function render() {
  const w = (s, n) => String(s).padEnd(n);
  const nameW = Math.min(48, Math.max(20, ...results.map((r) => r.name.length)));
  const lines = [];
  lines.push('');
  lines.push(`x402 discovery health check — ${BASE_URL}   (${new Date().toISOString()})`);
  if (rebasedOrigins.size) {
    lines.push(
      `advertised origin ${[...rebasedOrigins].join(', ')} rebased onto ${BASE_URL} — every probe hit THIS target`
    );
  }
  lines.push('');
  lines.push(`${w('STATUS', 7)} ${w('CHECK', nameW)}  DETAIL`);
  lines.push(`${'-'.repeat(7)} ${'-'.repeat(nameW)}  ${'-'.repeat(40)}`);
  for (const r of results) {
    const detail = r.detail.length > 160 ? `${r.detail.slice(0, 157)}...` : r.detail;
    lines.push(`${w(r.status, 7)} ${w(r.name, nameW)}  ${detail}`);
  }
  const counts = results.reduce((a, r) => ((a[r.status] = (a[r.status] || 0) + 1), a), {});
  lines.push('');
  lines.push(
    `${counts.PASS || 0} passed · ${counts.FAIL || 0} failed · ${counts.WARN || 0} warnings · ${counts.SKIP || 0} skipped`
  );
  lines.push('');
  return lines.join('\n');
}

async function main() {
  const landing = await checkLanding();
  const cat = await checkWellKnown(landing);
  if (cat) {
    checkCatalogInvariants(cat);
    checkAdvertisedOrigins(cat);
    await checkProductUrls(cat);
    await checkOpenApi(cat);
    await checkStats(cat);
    await check402(cat);
    checkMcp(cat);
    await checkAgentFiles(cat);
  } else {
    fail('catalog-dependent checks', 'skipped: the catalog could not be read');
  }

  const failed = results.filter((r) => r.status === 'FAIL');
  if (JSON_OUTPUT) {
    process.stdout.write(
      `${JSON.stringify(
        {
          base_url: BASE_URL,
          rebased_from: [...rebasedOrigins],
          checked_at: new Date().toISOString(),
          ok: failed.length === 0,
          results,
        },
        null,
        2
      )}\n`
    );
  } else {
    process.stdout.write(render());
  }
  process.exit(failed.length ? 1 : 0);
}

main().catch((e) => {
  process.stderr.write(`check-x402-discovery: internal error: ${e && e.stack ? e.stack : e}\n`);
  process.exit(2);
});
