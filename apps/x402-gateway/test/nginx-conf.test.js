'use strict';
/**
 * The shipped nginx location set is part of the product, not decoration: it
 * is what a paid request actually meets in production. Two failure classes
 * recur every time a product is added and the conf is not, and both are
 * silent until a buyer hits them:
 *
 *   1. BODY CAP — a location whose `client_max_body_size` is below the
 *      gateway's own cap makes the advertised limit unreachable. The catalog,
 *      the 402 schema and the docs promise (say) 10,000 items; nginx answers
 *      a bare HTML 413 at ~150 and the buyer never sees the structured
 *      `too_many_items` JSON with its `caps` block.
 *   2. READ TIMEOUT — the facilitator settle budget alone is 75 s
 *      (src/middleware.js). A paid route whose location times out at 30 s
 *      gets cut off WHILE the USDC settles: the buyer is charged and the
 *      response is thrown away.
 *
 * So this test walks the real registry against the real conf file with a
 * faithful implementation of nginx's own location-selection rules, and fails
 * if any product route falls into the catch-all or is under-provisioned.
 */

const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

const { buildTestGateway } = require('./gateway-helpers');

const CONF = path.join(__dirname, '..', 'nginx', 'animica-dev-x402.conf');
const CATCH_ALL = '/x402/';

/** The gateway's own default when a product declares no maxBodyBytes. */
const GATEWAY_DEFAULT_MAX_BODY = 64 * 1024;
/** Facilitator settle budget (src/middleware.js settleBudget) + headroom. */
const MIN_PAID_READ_TIMEOUT_S = 120;

function parseSize(v) {
  const m = /^(\d+)([kKmM]?)$/.exec(String(v).trim());
  if (!m) throw new Error(`unparsable size ${v}`);
  const n = Number(m[1]);
  return m[2].toLowerCase() === 'k' ? n * 1024 : m[2].toLowerCase() === 'm' ? n * 1024 * 1024 : n;
}

function parseSeconds(v) {
  const m = /^(\d+)(ms|s|m)?$/.exec(String(v).trim());
  if (!m) throw new Error(`unparsable time ${v}`);
  const n = Number(m[1]);
  return m[2] === 'ms' ? n / 1000 : m[2] === 'm' ? n * 60 : n;
}

/** Parse `location [mod] uri { ... }` blocks (one level, no nesting here). */
function parseLocations(text) {
  const out = [];
  const re = /^location\s+(?:(=|\^~|~\*|~)\s+)?(\S+)\s*\{/gm;
  let m;
  while ((m = re.exec(text)) !== null) {
    const start = re.lastIndex;
    let depth = 1;
    let i = start;
    while (i < text.length && depth > 0) {
      if (text[i] === '{') depth++;
      else if (text[i] === '}') depth--;
      i++;
    }
    const bodyRaw = text.slice(start, i - 1);
    // strip comments so a commented-out directive is never counted
    const body = bodyRaw.replace(/#[^\n]*/g, '');
    const directive = (name) => {
      const d = new RegExp(`^\\s*${name}\\s+([^;]+);`, 'm').exec(body);
      return d ? d[1].trim() : null;
    };
    const zone = /limit_req\s+zone=(\w+)/.exec(body);
    out.push({
      modifier: m[1] || '',
      uri: m[2],
      maxBodyBytes: directive('client_max_body_size') ? parseSize(directive('client_max_body_size')) : null,
      readTimeoutS: directive('proxy_read_timeout') ? parseSeconds(directive('proxy_read_timeout')) : null,
      proxyPass: directive('proxy_pass'),
      zone: zone ? zone[1] : null,
    });
  }
  return out;
}

/** nginx's own selection order: `=`, then longest prefix (`^~` wins outright), then regex. */
function selectLocation(locations, uri) {
  const exact = locations.find((l) => l.modifier === '=' && l.uri === uri);
  if (exact) return exact;
  let bestPrefix = null;
  for (const l of locations) {
    if (l.modifier !== '' && l.modifier !== '^~') continue;
    if (!uri.startsWith(l.uri)) continue;
    if (!bestPrefix || l.uri.length > bestPrefix.uri.length) bestPrefix = l;
  }
  if (bestPrefix && bestPrefix.modifier === '^~') return bestPrefix;
  for (const l of locations) {
    if (l.modifier !== '~' && l.modifier !== '~*') continue;
    const re = new RegExp(l.uri, l.modifier === '~*' ? 'i' : '');
    if (re.test(uri)) return l;
  }
  return bestPrefix;
}

/** A concrete request path for a route whose path carries {placeholders}. */
function samplePath(p) {
  return p.replace(/\{[^}]+\}/g, 'rc_' + 'ab'.repeat(16));
}

test('nginx: every product route has its own location — nothing falls into the catch-all', async () => {
  const conf = fs.readFileSync(CONF, 'utf8');
  const locations = parseLocations(conf);
  const t = await buildTestGateway();
  try {
    const rows = [];
    for (const p of t.gw.registry.products) {
      for (const r of p.routes) {
        rows.push({ product: p, path: samplePath(r.path), paid: true, method: r.method });
      }
      for (const r of p.freeRoutes || []) {
        rows.push({ product: p, path: samplePath(r.path), paid: false, method: r.method });
      }
    }
    assert.ok(rows.length >= 12, `expected the full product surface, saw ${rows.length} routes`);

    const problems = [];
    for (const row of rows) {
      const loc = selectLocation(locations, row.path);
      if (!loc) {
        problems.push(`${row.method} ${row.path}: no location matches at all`);
        continue;
      }
      if (loc.uri === CATCH_ALL && loc.modifier === '^~') {
        problems.push(`${row.method} ${row.path} (${row.product.id}) falls into the catch-all ^~ /x402/`);
        continue;
      }
      const appCap = row.product.maxBodyBytes || GATEWAY_DEFAULT_MAX_BODY;
      if (row.paid && loc.maxBodyBytes !== null && loc.maxBodyBytes < appCap) {
        problems.push(
          `${row.method} ${row.path} (${row.product.id}): nginx client_max_body_size ${loc.maxBodyBytes} < the gateway's own ${appCap} — nginx would 413 a request the product advertises as valid`);
      }
      if (row.paid && loc.readTimeoutS !== null && loc.readTimeoutS < MIN_PAID_READ_TIMEOUT_S) {
        problems.push(
          `${row.method} ${row.path} (${row.product.id}): proxy_read_timeout ${loc.readTimeoutS}s < ${MIN_PAID_READ_TIMEOUT_S}s — a settlement running past it is charged and discarded`);
      }
      if (loc.proxyPass !== 'http://127.0.0.1:8742') {
        problems.push(`${row.method} ${row.path}: proxy_pass is ${loc.proxyPass}, expected the gateway`);
      }
    }
    assert.deepEqual(problems, [], problems.join('\n'));
  } finally {
    await t.close();
  }
});

test('nginx: the free reveal keeps its own cheap block, and the facilitator is never proxied', async () => {
  const conf = fs.readFileSync(CONF, 'utf8');
  const locations = parseLocations(conf);

  const reveal = selectLocation(locations, '/x402/random/reveal/rc_' + 'ab'.repeat(16));
  assert.equal(reveal.uri, '/x402/random/reveal/');
  assert.equal(reveal.zone, 'x402_catalog', 'a free audit route belongs on the cheap zone');

  // The paid randomness family sits behind the paid zone with room for the
  // 10,000-item bodies the catalog advertises.
  const pick = selectLocation(locations, '/x402/random/pick');
  assert.equal(pick.zone, 'x402_paid');
  assert.ok(pick.maxBodyBytes >= 512_000, `pick body cap ${pick.maxBodyBytes}`);
  assert.ok(pick.readTimeoutS >= MIN_PAID_READ_TIMEOUT_S);

  // Discovery is free and cached; healthz is trivial.
  assert.equal(selectLocation(locations, '/x402').uri, '/x402');
  assert.equal(selectLocation(locations, '/.well-known/x402').uri, '/.well-known/x402');

  // The facilitator (127.0.0.1:8743) must never be reachable through nginx.
  for (const l of locations) {
    assert.doesNotMatch(String(l.proxyPass), /8743/, `${l.uri} proxies the facilitator`);
  }
  // ...and neither must /metrics.
  assert.equal(locations.find((l) => l.uri.includes('metrics')), undefined);
});
