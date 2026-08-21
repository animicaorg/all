'use strict';
/**
 * Guards for the claims this app makes about itself. Every one of these was
 * a real defect found by review, not a hypothetical:
 *
 *   - the README/docs said "self-hosted, no Coinbase services anywhere"
 *     while the DEFAULT facilitator was a third-party hosted endpoint;
 *   - "CDP" survived in comments and the env template after the standing
 *     directive to remove it;
 *   - the qrng description read "Quantum-attested when hardware providers
 *     are connected" while the source is os.urandom with attested:false, and
 *     nothing said so until after payment.
 *
 * Prose drifts from behaviour silently, so the claims are asserted here
 * against the code that has to make them true.
 */

const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

const cfgMod = require('../src/config');
const { buildTestGateway, request } = require('./gateway-helpers');

const APP = path.join(__dirname, '..');
const DOCS = path.join(APP, '..', '..', 'docs', 'x402.md');

/** Every shipped file: source, config, deployment examples, docs. */
function shippedFiles() {
  const out = [];
  const walk = (dir) => {
    for (const e of fs.readdirSync(dir, { withFileTypes: true })) {
      if (e.name === 'node_modules' || e.name === 'state' || e.name.startsWith('.git')) continue;
      const p = path.join(dir, e.name);
      if (e.isDirectory()) walk(p);
      else if (/\.(js|mjs|json|md|conf|service|example)$|^\.env\.example$|^animica-x402$/.test(e.name)) out.push(p);
    }
  };
  for (const d of ['src', 'nginx', 'systemd', 'bin']) walk(path.join(APP, d));
  out.push(path.join(APP, '.env.example'), path.join(APP, 'README.md'), path.join(APP, 'package.json'), DOCS);
  return out;
}

const CODE_AND_CONFIG = (p) => /\/(src|nginx|systemd|bin)\/|\.env\.example$/.test(p);

// SETTLEMENT INDEPENDENCE — REVERSED BY THE OPERATOR 2026-08-19.
//
// This guard used to assert that no third-party facilitator could ever settle.
// The operator directed the opposite ("switch all endpoints through the cdp
// facilitator") after we measured that we appear in ZERO of 15,125 Bazaar
// resources, and that the Bazaar indexes an endpoint only after the CDP
// facilitator itself settles a payment for it. Being listed there and settling
// elsewhere are mutually exclusive, so the property had to go to buy the
// listing. That was their call, made with the trade-off stated.
//
// What is still worth guarding, and what these tests now assert:
//   1. Third-party settlement is only ever reached when NAMED EXPLICITLY in
//      configuration. No default, no fallback, no implicit third party.
//   2. Pointing at CDP without credentials is refused AT BOOT, because a
//      gateway that quotes payments it cannot settle passes every health check
//      while failing every payment.
//   3. Discovery metadata stays OUT of the money path — see the third test.
const DISCOVERY_ONLY = /discovery\/resources/;

/**
 * `cfgMod.load()` reads process.env directly (its `overrides` argument does not
 * feed the env helper), so these set and restore real environment variables.
 */
function withEnv(vars, fn) {
  const saved = {};
  for (const [k, v] of Object.entries(vars)) {
    saved[k] = process.env[k];
    if (v === undefined) delete process.env[k];
    else process.env[k] = v;
  }
  try {
    return fn();
  } finally {
    for (const [k, v] of Object.entries(saved)) {
      if (v === undefined) delete process.env[k];
      else process.env[k] = v;
    }
  }
}

test('third-party settlement is reachable only when explicitly configured', () => {
  // The default must still be ours, with nothing set.
  withEnv({
    X402_FACILITATOR_MODE: undefined,
    X402_FACILITATOR_URL: undefined,
    X402_EVM_FACILITATOR_URL: undefined,
    X402_CDP_API_KEY_ID: undefined,
    X402_CDP_API_KEY_SECRET: undefined,
  }, () => {
    const cfg = cfgMod.load();
    assert.equal(cfg.facilitatorMode, 'self');
    assert.equal(cfg.evmFacilitatorUrl, `http://127.0.0.1:${cfg.evmFacilitatorPort}`);
    assert.equal(cfg.cdpApiKeyId, '');
    assert.equal(cfg.cdpApiKeySecret, '');
  });

  // remote mode still requires an explicitly named URL — never a default.
  withEnv({
    X402_FACILITATOR_MODE: 'remote',
    X402_FACILITATOR_URL: undefined,
    X402_EVM_FACILITATOR_URL: undefined,
  }, () => {
    assert.throws(() => cfgMod.load(), /requires X402_FACILITATOR_URL/,
      'remote mode must refuse to guess a facilitator');
  });
});

test('a CDP facilitator URL without credentials is refused at boot, not at payment time', () => {
  // The failure this prevents: every 402 quoted normally, every settle 401,
  // health checks green. Same silent-break class as the payTo mismatch that
  // took settlement down in August.
  withEnv({
    X402_FACILITATOR_MODE: 'remote',
    X402_FACILITATOR_URL: 'https://api.cdp.coinbase.com/platform/v2/x402',
    X402_EVM_FACILITATOR_URL: undefined,
    X402_CDP_API_KEY_ID: undefined,
    X402_CDP_API_KEY_SECRET: undefined,
  }, () => {
    assert.throws(() => cfgMod.load(), /X402_CDP_API_KEY_ID/,
      'pointing at CDP without a key must not boot');
  });

  withEnv({
    X402_FACILITATOR_MODE: 'remote',
    X402_FACILITATOR_URL: 'https://api.cdp.coinbase.com/platform/v2/x402',
    X402_EVM_FACILITATOR_URL: undefined,
    X402_CDP_API_KEY_ID: 'id',
    X402_CDP_API_KEY_SECRET: 'secret',
  }, () => {
    const ok = cfgMod.load();
    assert.equal(ok.facilitatorMode, 'remote');
    assert.equal(ok.evmFacilitatorUrl, 'https://api.cdp.coinbase.com/platform/v2/x402');
  });
});

test('discovery metadata never becomes part of payment binding', () => {
  // `protocol.requirementsEqual` compares accepts entries by canonical JSON to
  // decide whether a presented payment matches this route's offer. Anything
  // route-specific inside an accepts entry therefore becomes a payment-binding
  // rule. Putting Bazaar metadata there once made two same-priced products
  // non-interchangeable — which LOOKS like a security win but silently ties a
  // money property to a discovery flag, so turning discovery off would reopen
  // the hole with no test failing. Discovery metadata rides on the challenge,
  // never on an accepts entry.
  const mw = fs.readFileSync(path.join(APP, 'src', 'middleware.js'), 'utf8');
  const fn = /function buildAccepts\(route, cfg\)[\s\S]*?\n\}/.exec(mw);
  assert.ok(fn, 'buildAccepts must remain findable');
  assert.doesNotMatch(fn[0], /bazaar|discoverable|discovery/i,
    'no discovery metadata may appear inside an accepts entry');
});

test('no Coinbase SDK is pulled into the money path', () => {
  // Settlement moved to their facilitator; the dependency surface did not.
  // The JWT is implemented against the documented format with node:crypto in
  // src/facilitator-cdp/auth.js — three audited dependencies stay three.
  const pkg = JSON.parse(fs.readFileSync(path.join(APP, 'package.json'), 'utf8'));
  const deps = Object.keys(pkg.dependencies || {});
  assert.deepEqual(deps.filter((d) => /coinbase/i.test(d)), []);
  assert.equal(deps.length, 3, `dependency surface grew to ${deps.length}: ${deps.join(', ')}`);
  for (const file of shippedFiles()) {
    const text = fs.readFileSync(file, 'utf8');
    assert.doesNotMatch(text, /require\(['"]@coinbase\/|from ['"]@coinbase\//,
      `${path.relative(APP, file)} imports a Coinbase SDK`);
  }
});

test('the Bazaar directory is used read-only, and only for discovery', () => {
  const mesh = fs.readFileSync(path.join(APP, 'src', 'products', 'mesh-index.js'), 'utf8');
  assert.match(mesh, /discovery\/resources/, 'the Bazaar URL must be the discovery endpoint');
  const meshRuntime = fs.readFileSync(path.join(APP, 'src', 'products', 'mesh.js'), 'utf8');
  // Exactly one function in the Mesh runtime reaches a directory. Assert on
  // THAT function rather than pattern-matching the whole file, which cannot
  // distinguish a prose mention of "Bazaar" from a write to it.
  const fn = /async function fetchJson\([\s\S]*?\n  \}/.exec(meshRuntime);
  assert.ok(fn, 'fetchJson is the only directory caller and must remain findable');
  assert.doesNotMatch(fn[0], /method\s*:/, 'the directory fetch must not set a method — GET only');
  assert.doesNotMatch(fn[0], /body\s*:/, 'the directory fetch must not send a body');
  const callers = meshRuntime.match(/fetchImpl\(/g) || [];
  assert.equal(callers.length, 1, 'only fetchJson may call out; a second call site needs its own review');
});

test('the "self-hosted by default" claim in README/docs is what the config actually does', () => {
  const readme = fs.readFileSync(path.join(APP, 'README.md'), 'utf8');
  const docs = fs.readFileSync(DOCS, 'utf8');
  // The claim...
  assert.match(readme, /facilitator SELF-HOSTED/);
  assert.match(docs, /X402_FACILITATOR_MODE=self/);
  // ...and the behaviour that has to make it true, with nothing set.
  const cfg = cfgMod.load();
  assert.equal(cfg.facilitatorMode, 'self');
  assert.equal(cfg.evmFacilitatorUrl, `http://127.0.0.1:${cfg.evmFacilitatorPort}`);
  // The deployment example must not leave it to a default either.
  const unit = fs.readFileSync(path.join(APP, 'systemd', 'animica-x402.service'), 'utf8');
  const envExample = fs.readFileSync(path.join(APP, '.env.example'), 'utf8');
  assert.match(unit + envExample, /X402_FACILITATOR_MODE=self/);
});

test('the entropy trust model is knowable for FREE, before paying', async () => {
  const t = await buildTestGateway();
  try {
    // 1. free catalog
    const cat = await request(t.baseUrl, '/x402');
    const qrng = cat.json.products.find((p) => p.id === 'qrng');
    assert.equal(qrng.entropy.source, 'software-fallback');
    assert.equal(qrng.entropy.is_quantum, false);
    assert.equal(qrng.entropy.attested, false);

    // 2. the 402 offer itself, in the discovery extension an indexer reads.
    //    This lives under `discovery`, the vendor-neutral key that carries our
    //    full descriptor block. It is deliberately NOT asserted on `bazaar`:
    //    since 2026-08-20 that key carries CDP's dialect, whose `info` must
    //    validate against a sibling schema, so it holds input/output and
    //    nothing else. The disclosure must stay free and pre-purchase — which
    //    key it rides on is an encoding detail, so the test follows it rather
    //    than pinning the gateway to a shape a consumer rejects.
    const res = await request(t.baseUrl, '/x402/qrng/draw');
    assert.equal(res.status, 402);
    const protocol = require('../src/protocol');
    const required = protocol.decodeHeader(res.headers.get('payment-required'));
    assert.equal(required.extensions.discovery.info.entropy.is_quantum, false);
    assert.equal(required.extensions.discovery.info.entropy.attested, false);
    assert.equal(required.extensions.discovery.info.entropy.source, 'software-fallback');

    // 2b. And the DESCRIPTION carries it too, in plain words. This is the field
    //     every directory copies into its listing, so it is the one place the
    //     trust model reaches a buyer who never reads an extension block at all.
    //     Without this, moving the disclosure between extension keys could
    //     quietly remove it from every catalog that lists us.
    assert.match(qrng.description, /is_quantum=false/);
    assert.match(qrng.description, /attested=false/);

    // 3. and no description anywhere in the tree promises the opposite
    for (const p of cat.json.products) {
      assert.doesNotMatch(p.description, /quantum-attested|hardware quantum|true random(ness)? from quantum/i, p.id);
    }
  } finally {
    await t.close();
  }
});

test('commit-reveal does not claim to prove more than it proves', async () => {
  const t = await buildTestGateway();
  try {
    const cat = await request(t.baseUrl, '/x402');
    const commit = cat.json.products.find((p) => p.id === 'random_commit');
    // The old copy said "provably fair" with no trust model attached.
    assert.match(commit.description, /does NOT prove/);

    const docs = fs.readFileSync(DOCS, 'utf8');
    assert.doesNotMatch(docs, /proof the secret was not cherry-picked/i);
    assert.match(docs, /does NOT prove/);

    const src = fs.readFileSync(path.join(APP, 'src', 'products', 'random.js'), 'utf8');
    assert.doesNotMatch(src, /check the secret was not cherry-picked/);
  } finally {
    await t.close();
  }
});
