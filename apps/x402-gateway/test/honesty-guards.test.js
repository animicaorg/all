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

test('no Coinbase dependency, name or URL survives anywhere in the shipped tree', () => {
  const offences = [];
  for (const file of shippedFiles()) {
    const text = fs.readFileSync(file, 'utf8');
    const rel = path.relative(APP, file);
    text.split('\n').forEach((line, i) => {
      const where = `${rel}:${i + 1}`;
      // 1. No third-party facilitator/service URLs, ever — this is the one
      //    that silently settles real money somewhere else.
      if (/https?:\/\/[^\s"')]*(coinbase|x402\.org|cdp\.)/i.test(line)) {
        offences.push(`${where}: third-party service URL — ${line.trim()}`);
      }
      // 2. "CDP" as a product name is out per the standing directive; the
      //    neutral phrasing is "x402 v2 §7-compatible" / "x402 discovery
      //    indexers". (CDP inside a longer word is not a hit.)
      if (/\bCDP\b/.test(line)) {
        offences.push(`${where}: CDP reference — ${line.trim()}`);
      }
      // 3. The word may appear ONLY as the negative claim in the two status
      //    docs; never in code, config or deployment files.
      if (/coinbase/i.test(line)) {
        if (CODE_AND_CONFIG(file) || !/no Coinbase/i.test(line)) {
          offences.push(`${where}: Coinbase mention — ${line.trim()}`);
        }
      }
    });
  }
  assert.deepEqual(offences, [], offences.join('\n'));
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

    // 2. the 402 offer itself, in the discovery extension an indexer reads
    const res = await request(t.baseUrl, '/x402/qrng/draw');
    assert.equal(res.status, 402);
    const protocol = require('../src/protocol');
    const required = protocol.decodeHeader(res.headers.get('payment-required'));
    assert.equal(required.extensions.bazaar.info.entropy.is_quantum, false);
    assert.equal(required.extensions.bazaar.info.entropy.attested, false);
    assert.equal(required.extensions.bazaar.info.entropy.source, 'software-fallback');

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
