'use strict';
/**
 * The randomness family (spec §A): random_int, random_shuffle, random_pick,
 * random_bulk, random_commit + the FREE public reveal.
 *
 * What these tests hold down, per product:
 *   - payment is required (402 with the registry's price, no free rides);
 *   - caps are enforced BEFORE settlement (400 with no `payment-required`
 *     header and zero facilitator calls — we never charge for work we will
 *     then refuse);
 *   - the derivation is EXACTLY recomputable from a fixed byte string. The
 *     golden vectors below were produced by the repo's own canonical
 *     verifier, `randomness/beacon_api/static/verify.js` (the same file the
 *     responses point buyers at), and when that file is present the tests
 *     ALSO re-derive every answer through it live. A change to derive.js
 *     that drifts from the canonical DRNG fails here;
 *   - the honesty fields (source/health/attestation/verification) are on
 *     every response, `is_quantum` and `attested` pass through as the false
 *     they currently are, and an unhealthy source refuses the whole family;
 *   - reveal is free, needs no payment, is idempotent, and its secret
 *     really opens the commitment.
 */

const { test } = require('node:test');
const assert = require('node:assert/strict');
const crypto = require('node:crypto');

const { sha3_256 } = require('@noble/hashes/sha3.js');
const protocol = require('../src/protocol');
const derive = require('../src/products/derive');
const { QRNG_FIXTURE, buildTestGateway, request, paidRequest, chainHandlers, fakeNodeFetch } = require('./gateway-helpers');

/**
 * The repo's canonical, dependency-free verifier. Present in a full repo
 * checkout; absent if this app is vendored alone, in which case the golden
 * vectors below still pin every derivation.
 */
let AnimicaBeacon = null;
try {
  // eslint-disable-next-line global-require, import/no-unresolved
  AnimicaBeacon = require('../../../randomness/beacon_api/static/verify.js');
} catch { /* vendored without the repo — golden vectors carry the test */ }

const ENTROPY_HEX = QRNG_FIXTURE.bytes_hex; // the REAL captured node draw
const ENTROPY = Buffer.from(ENTROPY_HEX, 'hex');

/**
 * Golden vectors: produced with verify.js (AnimicaBeacon.compute) over
 * ENTROPY_HEX and request_id "gv-1". These are the contract — if derive.js
 * ever stops reproducing them, every published `derivation` block in the
 * wild becomes a lie.
 */
const GOLDEN = {
  requestId: 'gv-1',
  rangeSeedHex: '5cfeb2e723bbd713de865705505f9497c08f259b2544ecaf81bb5e9b39420ad0',
  range_1_6_10: [3, 1, 4, 1, 2, 1, 5, 3, 2, 5],
  shuffle8: [0, 1, 3, 2, 4, 5, 6, 7],
  lottery10k3: [9, 5, 0],
  weighted1234: 3,
  choice10: 5,
};

/** JSON POST helper. */
function post(baseUrl, path, body, opts = {}) {
  return paidRequest(baseUrl, path, Object.assign({
    method: 'POST',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify(body),
  }, opts));
}

function unpaidPost(baseUrl, path, body) {
  return request(baseUrl, path, {
    method: 'POST',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify(body),
  });
}

/**
 * Node handlers whose rand.quantumRandomBytes honours `n` (the shared
 * fixture always answers 32 bytes, which random_bulk must not accept) and
 * whose bytes DIFFER on every call — random_bulk now makes one node call per
 * draw, so distinct bytes per call are what makes the independence testable.
 * Each response carries a correct digest over its own bytes.
 */
function rampHandlers({ health = { passed: true, min_entropy_per_byte: 7.8078 } } = {}) {
  let call = 0;
  return Object.assign(chainHandlers(), {
    'rand.quantumRandomBytes': (p) => {
      const n = p.n;
      const seq = call++;
      const buf = Buffer.alloc(n);
      for (let i = 0; i < n; i++) buf[i] = (i + 37 * seq) % 256;
      return {
        bytes_hex: buf.toString('hex'),
        n,
        source: QRNG_FIXTURE.source,
        health,
        attestation: Object.assign({}, QRNG_FIXTURE.attestation, {
          digest_hex: Buffer.from(sha3_256(buf)).toString('hex'),
        }),
      };
    },
  });
}

/** Every randomness response must carry the same honesty block, verbatim. */
function assertHonest(body) {
  assert.deepEqual(body.source, QRNG_FIXTURE.source);
  assert.equal(body.source.is_quantum, false);
  assert.equal(body.source.is_hardware, false);
  assert.equal(body.health.passed, true);
  assert.equal(body.attestation.attested, false);
  assert.equal(body.verification.attested, false);
  assert.equal(body.verification.method, 'signed-digest-attestation');
  assert.match(body.verification.verifier, /verify\.js/);
  // nothing anywhere may claim hardware/quantum attestation
  assert.doesNotMatch(JSON.stringify(body.verification), /quantum-attested|hardware quantum/i);
}

// ------------------------------------------------- derivation (pure units)

test('derive: golden vectors match the canonical verify.js DRNG byte-for-byte', () => {
  assert.equal(derive.bytesToHex(derive.drngSeed(ENTROPY, 'range', GOLDEN.requestId)), GOLDEN.rangeSeedHex);

  const ints = derive.uniformInts({ entropy: ENTROPY, requestId: GOLDEN.requestId, lo: 1, hi: 6, count: 10 });
  assert.deepEqual(ints.output, GOLDEN.range_1_6_10);
  // 10 draws over a 6-wide range: 2 bytes per attempt, no rejection here
  assert.equal(ints.rng.bytesConsumed, 20);

  assert.deepEqual(
    derive.shuffleIndices({ entropy: ENTROPY, requestId: GOLDEN.requestId, n: 8 }).output,
    GOLDEN.shuffle8);
  assert.deepEqual(
    derive.sampleIndices({ entropy: ENTROPY, requestId: GOLDEN.requestId, n: 10, k: 3 }).output,
    GOLDEN.lottery10k3);
  assert.deepEqual(
    derive.weightedIndices({ entropy: ENTROPY, requestId: GOLDEN.requestId, weights: [1, 2, 3, 4], k: 1, replace: true }).output,
    [GOLDEN.weighted1234]);
  assert.deepEqual(
    derive.uniformIndices({ entropy: ENTROPY, requestId: GOLDEN.requestId, n: 10, k: 1 }).output,
    [GOLDEN.choice10]);
});

test('derive: live cross-check against randomness/beacon_api/static/verify.js', { skip: AnimicaBeacon ? false : 'repo verifier not available in this checkout' }, () => {
  const e = new Uint8Array(ENTROPY);
  const rid = 'cross-1';
  assert.deepEqual(
    AnimicaBeacon.compute('range', e, 0, rid, { lo: -5, hi: 5, count: 25 }).output,
    derive.uniformInts({ entropy: ENTROPY, requestId: rid, lo: -5, hi: 5, count: 25 }).output);
  const idx = Array.from({ length: 37 }, (_, i) => i);
  assert.deepEqual(
    AnimicaBeacon.compute('shuffle', e, 0, rid, { items: idx }).output,
    derive.shuffleIndices({ entropy: ENTROPY, requestId: rid, n: 37 }).output);
  assert.deepEqual(
    AnimicaBeacon.compute('lottery', e, 0, rid, { entries: idx, k: 5 }).output,
    derive.sampleIndices({ entropy: ENTROPY, requestId: rid, n: 37, k: 5 }).output);
  assert.equal(
    AnimicaBeacon.compute('weighted', e, 0, rid, { items: idx, weights: idx.map((i) => i + 1) }).output,
    derive.weightedIndices({ entropy: ENTROPY, requestId: rid, weights: idx.map((i) => i + 1), k: 1, replace: true }).output[0]);
});

test('derive: rejection sampling is unbiased and byte consumption is the documented rule', () => {
  // A range that does NOT divide 2^(8k) is exactly where modulo bias would
  // show up: 3 over 2 bytes (65536 % 3 == 1) — one value in 65536 rejected.
  const counts = new Map();
  for (let i = 0; i < 3000; i++) {
    const v = derive.uniformInts({ entropy: ENTROPY, requestId: `bias-${i}`, lo: 0, hi: 2, count: 1 }).output[0];
    counts.set(v, (counts.get(v) || 0) + 1);
  }
  assert.deepEqual([...counts.keys()].sort(), [0, 1, 2]);
  for (const v of counts.values()) {
    // 1000 expected each; a 25% band is far outside sampling noise but well
    // inside what a modulo-biased mapping over this range would produce.
    assert.ok(v > 750 && v < 1250, `uniformity out of band: ${[...counts.entries()]}`);
  }
  // n <= 1 consumes nothing; a 6-wide range consumes 2 bytes per accepted draw.
  const one = derive.uniformInts({ entropy: ENTROPY, requestId: 'x', lo: 7, hi: 7, count: 4 });
  assert.deepEqual(one.output, [7, 7, 7, 7]);
  assert.equal(one.rng.bytesConsumed, 0);
});

// ----------------------------------------------------------- catalog / 402

test('random family: catalog lists all five at spec prices with the free reveal advertised', async () => {
  const t = await buildTestGateway();
  try {
    const cat = await request(t.baseUrl, '/x402');
    const byId = Object.fromEntries(cat.json.products.map((p) => [p.id, p]));
    assert.equal(byId.random_int.price, '0.05');
    assert.equal(byId.random_int.price_atomic, '50000');
    assert.equal(byId.random_shuffle.price, '0.05');
    assert.equal(byId.random_pick.price, '0.05');
    assert.equal(byId.random_bulk.price, '0.20');
    assert.equal(byId.random_commit.price, '0.10');
    for (const id of ['random_int', 'random_shuffle', 'random_pick', 'random_bulk', 'random_commit']) {
      assert.equal(byId[id].available, true, `${id} should be available`);
      // input schema so the 402's discovery extension can advertise it
      assert.equal(byId[id].outputSchema.input.type, 'http');
      assert.equal(byId[id].outputSchema.input.method, 'POST');
      assert.ok(Object.keys(byId[id].outputSchema.input.bodyFields).length > 0);
    }
    // NO product description — qrng included, since it is the one that used
    // to read "Quantum-attested when hardware providers are connected" on a
    // skim — may mention quantum without stating the CURRENT truth in the
    // same breath.
    for (const id of ['qrng', 'random_int', 'random_shuffle', 'random_pick', 'random_bulk', 'random_commit']) {
      const d = byId[id].description;
      if (/\bquantum\b/i.test(d)) {
        assert.match(d, /is_quantum=false/,
          `${id} mentions quantum without stating the current is_quantum=false truth`);
        assert.match(d, /software/i, `${id} mentions quantum without naming the software source`);
      }
      assert.doesNotMatch(d, /quantum-attested|hardware quantum|quantum-grade/i, id);
    }
    // volume discount is real, measured against the CHEAPEST equivalent
    // purchase (N independent draws == N single qrng calls at the bulk
    // minimum), not against a strawman of 10 single calls.
    const minDraws = 6; // floor(50000/10000)+1, published in the schema
    assert.ok(
      BigInt(byId.random_bulk.price_atomic) < BigInt(byId.qrng.price_atomic) * BigInt(minDraws),
      'bulk must be cheaper than the smallest number of single draws it will accept');
    assert.match(byId.random_bulk.outputSchema.input.bodyFields.draws.description, /5\.\.10/);

    // Pre-purchase honesty: the free catalog states the entropy source, so
    // nobody has to pay to discover that it is a software CSPRNG.
    for (const id of ['qrng', 'random_int', 'random_shuffle', 'random_pick', 'random_bulk', 'random_commit']) {
      const e = byId[id].entropy;
      assert.ok(e, `${id} must disclose its entropy source in the catalog`);
      assert.equal(e.source, 'software-fallback');
      assert.equal(e.is_hardware, false);
      assert.equal(e.is_quantum, false);
      assert.equal(e.attested, false);
      assert.equal(e.health_passed, true);
      assert.equal(e.min_entropy_per_byte, 7.8078);
      assert.ok(e.observed_at, `${id} entropy disclosure must be timestamped`);
    }
    // the free reveal is discoverable, with an absolute URL an indexer can follow
    assert.deepEqual(byId.random_commit.free_endpoints, [{
      endpoint: 'GET /x402/random/reveal/{commit_id}',
      url: `${t.gw.cfg.resourceBaseUrl}/x402/random/reveal/{commit_id}`,
      price: '0',
      description: 'FREE public reveal: secret, salt, raw draw and attestation for a commitment. No payment, ever. Idempotent.',
    }]);
  } finally {
    await t.close();
  }
});

test('random family: every paid route demands payment first (402 with its own price)', async () => {
  const t = await buildTestGateway();
  const cases = [
    ['/x402/random/int', { min: 1, max: 6 }, '50000'],
    ['/x402/random/shuffle', { items: [1, 2, 3] }, '50000'],
    ['/x402/random/pick', { items: [1, 2, 3] }, '50000'],
    ['/x402/qrng/bulk', {}, '200000'],
    ['/x402/random/commit', {}, '100000'],
  ];
  try {
    for (const [path, body, amount] of cases) {
      const res = await unpaidPost(t.baseUrl, path, body);
      assert.equal(res.status, 402, `${path} must require payment`);
      const required = protocol.decodeHeader(res.headers.get('payment-required'));
      assert.equal(required.accepts[0].amount, amount, path);
      // Bazaar discovery extension carries the input schema
      assert.equal(required.extensions.bazaar.info.input.method, 'POST');
      assert.equal(res.json.x402Version, 1);
    }
    assert.equal(t.fac.calls.settle.length, 0);
  } finally {
    await t.close();
  }
});

// --------------------------------------------------------------- random_int

test('random_int: paid draw is exactly recomputable from the published bytes', async () => {
  const t = await buildTestGateway();
  try {
    const { paid } = await post(t.baseUrl, '/x402/random/int', { min: 1, max: 6, count: 10, request_id: GOLDEN.requestId });
    assert.equal(paid.status, 200);
    const b = paid.json;
    assert.deepEqual(b.result.ints, GOLDEN.range_1_6_10);
    assert.equal(b.randomness, ENTROPY_HEX);
    assert.equal(b.derivation.algorithm, 'uniform-int-rejection-sampling');
    assert.equal(b.derivation.seed_hex, GOLDEN.rangeSeedHex);
    assert.equal(b.derivation.stream_bytes_consumed, 20);
    assert.match(b.derivation.rules.uniform_int, /rejection sampling/);
    // the buyer recomputes from the raw bytes with the published rule
    const local = derive.uniformInts({ entropy: Buffer.from(b.randomness, 'hex'), requestId: b.derivation.request_id, lo: 1, hi: 6, count: 10 });
    assert.deepEqual(local.output, b.result.ints);
    // …or by dropping `recompute` straight into the repo verifier
    assert.deepEqual(b.derivation.recompute.params, { lo: 1, hi: 6, count: 10 });
    if (AnimicaBeacon) assert.equal(AnimicaBeacon.verifyResult(b.derivation.recompute), true);
    assertHonest(b);
    assert.equal(b.payment.amount_atomic, '50000');
    assert.equal(t.fac.calls.settle.length, 1);
  } finally {
    await t.close();
  }
});

test('random_int: ONE node draw per request, taken BEFORE settlement', async () => {
  const t = await buildTestGateway();
  try {
    const { paid } = await post(t.baseUrl, '/x402/random/int', { min: 0, max: 999, count: 500 });
    assert.equal(paid.status, 200);
    assert.equal(paid.json.result.ints.length, 500);
    // Events for the paid retry only: everything after the payment was
    // verified. Exactly ONE node draw covers all 500 integers — never one
    // node call per output item — and it lands before settlement.
    const verifyIdx = t.events.indexOf('fac:verify');
    const settleIdx = t.events.indexOf('fac:settle');
    const afterVerify = t.events.slice(verifyIdx + 1, settleIdx);
    assert.deepEqual(afterVerify, ['node:rand.quantumRandomBytes'], `paid path should be one draw, got ${t.events.join(',')}`);
    assert.ok(verifyIdx < settleIdx, 'the draw must precede settlement');
  } finally {
    await t.close();
  }
});

test('random_int: caps and bad params answer 400 with no payment demanded', async () => {
  const t = await buildTestGateway();
  const cases = [
    { min: 1, max: 6, count: 1001 },        // over the 1000 cap
    { min: 10, max: 1 },                     // max < min
    { min: 1 },                              // missing max
    { min: 1, max: 6, count: 0 },            // count < 1
    { min: 1.5, max: 6 },                    // non-integer
    { min: 1, max: 6, request_id: 'x'.repeat(200) },
  ];
  try {
    for (const body of cases) {
      const res = await unpaidPost(t.baseUrl, '/x402/random/int', body);
      assert.equal(res.status, 400, JSON.stringify(body));
      assert.equal(res.headers.get('payment-required'), null, JSON.stringify(body));
    }
    // a non-JSON body is also refused before payment
    const raw = await request(t.baseUrl, '/x402/random/int', { method: 'POST', headers: { 'content-type': 'text/plain' }, body: 'nope' });
    assert.equal(raw.status, 400);
    assert.equal(t.fac.calls.verify.length, 0);
    assert.equal(t.fac.calls.settle.length, 0);
  } finally {
    await t.close();
  }
});

// ----------------------------------------------------------- random_shuffle

test('random_shuffle: Fisher-Yates permutation, applied to the caller items', async () => {
  const t = await buildTestGateway();
  try {
    const items = ['a', 'b', 'c', 'd', 'e', 'f', 'g', 'h'];
    const { paid } = await post(t.baseUrl, '/x402/random/shuffle', { items, request_id: GOLDEN.requestId });
    assert.equal(paid.status, 200);
    const b = paid.json;
    assert.deepEqual(b.result.permutation, GOLDEN.shuffle8);
    assert.deepEqual(b.result.items, GOLDEN.shuffle8.map((i) => items[i]));
    // a permutation: every index exactly once
    assert.deepEqual([...b.result.permutation].sort((x, y) => x - y), items.map((_, i) => i));
    assert.equal(b.derivation.algorithm, 'fisher-yates-permutation');
    assert.match(b.derivation.rules.shuffle, /Fisher-Yates/);
    assert.deepEqual(
      derive.shuffleIndices({ entropy: Buffer.from(b.randomness, 'hex'), requestId: GOLDEN.requestId, n: 8 }).output,
      b.result.permutation);
    if (AnimicaBeacon) assert.equal(AnimicaBeacon.verifyResult(b.derivation.recompute), true);
    assertHonest(b);
  } finally {
    await t.close();
  }
});

test('random_shuffle: the 1..N form, and the caps that refuse before payment', async () => {
  const t = await buildTestGateway();
  try {
    const { paid } = await post(t.baseUrl, '/x402/random/shuffle', { n: 8, request_id: GOLDEN.requestId });
    assert.equal(paid.status, 200);
    assert.deepEqual(paid.json.result.items, GOLDEN.shuffle8.map((i) => i + 1));
    assert.deepEqual([...paid.json.result.items].sort((x, y) => x - y), [1, 2, 3, 4, 5, 6, 7, 8]);

    for (const body of [
      { items: new Array(10001).fill(0) }, // over the item cap
      { items: [] },                        // empty
      { n: 3, items: [1, 2, 3] },           // both forms
      {},                                    // neither form
      { n: 0 },
    ]) {
      const res = await unpaidPost(t.baseUrl, '/x402/random/shuffle', body);
      assert.equal(res.status, 400, JSON.stringify(body).slice(0, 60));
      assert.equal(res.headers.get('payment-required'), null);
    }
    assert.equal(t.fac.calls.settle.length, 1); // only the successful shuffle
  } finally {
    await t.close();
  }
});

// -------------------------------------------------------------- random_pick

test('random_pick: unweighted without replacement is a partial Fisher-Yates sample', async () => {
  const t = await buildTestGateway();
  try {
    const items = Array.from({ length: 10 }, (_, i) => `e${i}`);
    const { paid } = await post(t.baseUrl, '/x402/random/pick', { items, k: 3, request_id: GOLDEN.requestId });
    const b = paid.json;
    assert.equal(paid.status, 200);
    assert.deepEqual(b.result.indices, GOLDEN.lottery10k3);
    assert.deepEqual(b.result.picked, GOLDEN.lottery10k3.map((i) => items[i]));
    assert.equal(b.result.replace, false);
    assert.equal(b.result.weighted, false);
    assert.equal(new Set(b.result.indices).size, 3, 'no repeats without replacement');
    assert.equal(b.derivation.algorithm, 'partial-fisher-yates-sample');
    if (AnimicaBeacon) assert.equal(AnimicaBeacon.verifyResult(b.derivation.recompute), true);
    assertHonest(b);
  } finally {
    await t.close();
  }
});

test('random_pick: weighted picks follow the documented cumulative-weight search', async () => {
  const t = await buildTestGateway();
  try {
    const items = ['a', 'b', 'c', 'd'];
    const { paid } = await post(t.baseUrl, '/x402/random/pick', { items, k: 1, weights: [1, 2, 3, 4], request_id: GOLDEN.requestId });
    const b = paid.json;
    assert.equal(paid.status, 200);
    assert.deepEqual(b.result.indices, [GOLDEN.weighted1234]);
    assert.equal(b.result.weighted, true);
    assert.match(b.derivation.rules.weighted, /cumulative-weight search/);
    if (AnimicaBeacon) assert.equal(AnimicaBeacon.verifyResult(b.derivation.recompute), true);

    // with replacement, k > 1: no stock verify.js kind, so the steps carry it
    const many = await post(t.baseUrl, '/x402/random/pick', { items, k: 5, replace: true, weights: [1, 1, 1, 97] });
    assert.equal(many.paid.status, 200);
    assert.equal(many.paid.json.result.indices.length, 5);
    assert.equal(many.paid.json.derivation.recompute, null);
    assert.match(many.paid.json.derivation.recompute_note, /steps/);
    assert.deepEqual(
      derive.weightedIndices({
        entropy: Buffer.from(many.paid.json.randomness, 'hex'),
        requestId: '',
        weights: [1, 1, 1, 97],
        k: 5,
        replace: true,
      }).output,
      many.paid.json.result.indices);

    // without replacement the winners are distinct
    const distinct = await post(t.baseUrl, '/x402/random/pick', { items, k: 4, weights: [5, 5, 5, 5] });
    assert.equal(new Set(distinct.paid.json.result.indices).size, 4);
  } finally {
    await t.close();
  }
});

test('random_pick: weight/size violations are refused before settlement', async () => {
  const t = await buildTestGateway();
  const cases = [
    { items: ['a', 'b'], k: 3 },                             // k > items without replacement
    { items: ['a', 'b'], weights: [1] },                     // weight count mismatch
    { items: ['a', 'b'], weights: [1, 0.5] },                // float weight
    { items: ['a', 'b'], weights: [0, 0] },                  // zero total
    { items: ['a', 'b', 'c'], k: 2, weights: [7, 0, 0] },    // fewer positive weights than k
    { items: ['a'], k: 1, replace: 'yes' },                  // non-boolean
    { items: new Array(10001).fill(1) },                     // item cap
  ];
  try {
    for (const body of cases) {
      const res = await unpaidPost(t.baseUrl, '/x402/random/pick', body);
      assert.equal(res.status, 400, JSON.stringify(body).slice(0, 80));
      assert.equal(res.headers.get('payment-required'), null);
    }
    assert.equal(t.fac.calls.settle.length, 0, 'nothing may settle for a refused request');
  } finally {
    await t.close();
  }
});

// -------------------------------------------------------------- random_bulk

test('random_bulk: N INDEPENDENT draws (one node call + one attestation each), one settlement', async () => {
  const t = await buildTestGateway({ handlers: rampHandlers() });
  try {
    const { first, paid } = await post(t.baseUrl, '/x402/qrng/bulk', { draws: 10, bytes: 32 });
    assert.equal(first.status, 402);
    assert.equal(protocol.decodeHeader(first.headers.get('payment-required')).accepts[0].amount, '200000');
    assert.equal(paid.status, 200);
    const b = paid.json;
    assert.equal(b.result.count, 10);
    assert.equal(b.result.bytes_per_draw, 32);
    assert.equal(b.result.draws.length, 10);
    assert.equal(b.result.total_bytes, 320);

    // Every draw is its OWN draw: distinct bytes, its own signed digest.
    const seen = new Set();
    for (const d of b.result.draws) {
      assert.equal(d.bytes, 32);
      assert.equal(d.randomness.length, 64);
      assert.equal(d.sha3_256, Buffer.from(sha3_256(Buffer.from(d.randomness, 'hex'))).toString('hex'));
      assert.equal(d.attestation.digest_hex, d.sha3_256, 'the attestation covers THIS draw');
      assert.equal(d.attestation.attested, false, 'honest: software signer');
      assert.equal(d.health.passed, true);
      assert.equal(d.verification.method, 'signed-digest-attestation');
      seen.add(d.randomness);
    }
    assert.equal(seen.size, 10, 'ten distinct draws, not one buffer sliced ten ways');
    // and there is no concatenation being passed off as one attested draw
    assert.equal(b.randomness, undefined);
    assert.equal(b.attestation.scope, 'per_draw');
    assert.equal(b.attestation.count, 10);
    assert.equal(b.derivation.algorithm, 'independent-draws');
    assert.match(b.derivation.honesty, /10 node calls were made/);

    // 10 node draws for the paid request (plus this fixture's un-memoised
    // readiness probes — availabilityTtlMs is 0 here).
    assert.ok(t.events.filter((e) => e === 'node:rand.quantumRandomBytes').length >= 10);

    // The discount is stated in units that are real: independent draws.
    const p = b.result.pricing;
    assert.equal(p.price_atomic, '200000');
    assert.equal(p.price_atomic_per_draw, '20000');
    assert.equal(p.single_draw_price_atomic, '50000');
    assert.equal(p.equivalent_single_draw_cost_atomic, '500000');
    assert.equal(p.savings_atomic, '300000');
    assert.equal(p.min_draws_for_discount, 5);
    // ...and it does NOT pretend to be cheaper per byte, because it is not:
    // the cheapest way to buy 320 BYTES (regardless of attestation count) is
    // ceil(320/1024) = 1 single draw at 10000 atomic. The response says so
    // rather than leaving the buyer to work it out after paying.
    const cheapestByBytes = BigInt(Math.ceil(b.result.total_bytes / 1024)) * 10000n;
    assert.ok(BigInt(p.price_atomic) > cheapestByBytes);
    assert.equal(p.per_byte.cheaper_per_byte, 'single_max_draw');
    assert.match(p.per_byte.note, /single 1024-byte draw at \$0\.05 is cheaper/);

    assert.equal(t.fac.calls.settle.length, 1, 'ten draws, ONE settlement');
    assertHonest(b);
  } finally {
    await t.close();
  }
});

test('random_bulk: below the break-even draw count it refuses and names the cheaper endpoint', async () => {
  const t = await buildTestGateway({ handlers: rampHandlers() });
  try {
    // $0.20 for 1..4 draws is a PREMIUM over 1..4 single $0.05 draws, so it
    // is a 400 before any payment — never a settled "volume discount".
    for (const draws of [1, 2, 4]) {
      const res = await unpaidPost(t.baseUrl, '/x402/qrng/bulk', { draws, bytes: 32 });
      assert.equal(res.status, 400, `draws=${draws} must be refused`);
      assert.equal(res.json.error, 'below_bulk_minimum');
      assert.equal(res.json.min_draws, 5);
      assert.equal(res.json.cheaper_alternative.endpoint, 'GET /x402/qrng/draw');
      assert.equal(res.json.cheaper_alternative.price_atomic, '50000');
      assert.equal(res.headers.get('payment-required'), null, 'no terms are offered');
    }
    // 5 draws is the first count that is genuinely cheaper: 200000 < 250000.
    const ok = await post(t.baseUrl, '/x402/qrng/bulk', { draws: 5, bytes: 32 });
    assert.equal(ok.paid.status, 200);
    const p = ok.paid.json.result.pricing;
    assert.ok(BigInt(p.price_atomic) < BigInt(p.equivalent_single_draw_cost_atomic));
    assert.ok(BigInt(p.price_atomic_per_draw) < BigInt(p.single_draw_price_atomic));
    assert.equal(t.fac.calls.verify.length, 1, 'the refusals never reached the facilitator');
  } finally {
    await t.close();
  }
});

test('random_bulk: a price table where bulk can never beat single draws is not sold at all', async () => {
  // 10 max draws x $0.05 = $0.50, so a $1.00 bulk price can never be a
  // discount. The catalog says so and the route never emits a 402.
  const t = await buildTestGateway({
    handlers: rampHandlers(),
    overrides: { randomBulkPriceUsd: '1.00' },
  });
  try {
    const cat = await request(t.baseUrl, '/x402');
    const bulk = cat.json.products.find((p) => p.id === 'random_bulk');
    assert.equal(bulk.available, false);
    assert.equal(bulk.unavailable_reason, 'random_bulk_price_not_a_discount');
    // Every reachable draw count is now below the break-even, so the request
    // never even gets as far as the availability hook: validate() refuses it
    // first with the same 400 + cheaper_alternative pointer. Either way no
    // terms are offered and nothing is charged.
    const res = await unpaidPost(t.baseUrl, '/x402/qrng/bulk', { draws: 10, bytes: 32 });
    assert.equal(res.status, 400);
    assert.equal(res.json.error, 'below_bulk_minimum');
    assert.equal(res.json.min_draws, 21);
    assert.equal(res.headers.get('payment-required'), null);
    assert.equal(t.fac.calls.verify.length, 0);
  } finally {
    await t.close();
  }
});

test('random_bulk: over-cap requests are refused before payment', async () => {
  const t = await buildTestGateway({ handlers: rampHandlers() });
  try {
    for (const body of [{ draws: 11 }, { draws: 10, bytes: 99999 }, { draws: 0 }, { bytes: 0 }]) {
      const res = await unpaidPost(t.baseUrl, '/x402/qrng/bulk', body);
      assert.equal(res.status, 400, JSON.stringify(body));
      assert.equal(res.headers.get('payment-required'), null);
    }
    // draws*bytes over the total-byte cap is also pre-settlement
    const big = await buildTestGateway({ handlers: rampHandlers(), overrides: { randomMaxDrawBytes: 64 } });
    try {
      const res = await unpaidPost(big.baseUrl, '/x402/qrng/bulk', { draws: 10, bytes: 32 });
      assert.equal(res.status, 400);
      assert.equal(res.json.error, 'request_too_large');
      assert.equal(res.json.caps.max_total_bytes, 64);
      assert.equal(big.fac.calls.settle.length, 0);
    } finally {
      await big.close();
    }
    assert.equal(t.fac.calls.settle.length, 0);
  } finally {
    await t.close();
  }
});

// ------------------------------------------------------------ commit/reveal

test('random_commit: commit seals the draw, reveal is FREE and opens the commitment', async () => {
  const t = await buildTestGateway();
  try {
    const { first, paid } = await post(t.baseUrl, '/x402/random/commit', { memo: 'round-7', request_id: 'game-1' });
    assert.equal(first.status, 402);
    assert.equal(paid.status, 200);
    const c = paid.json;
    assert.match(c.commit_id, /^rc_[0-9a-f]{32}$/);
    assert.equal(c.algorithm, 'sha3_256(secret||salt)');
    assert.equal(typeof c.reveal_after, 'number');
    assert.equal(c.reveal_is_free, true);
    // the commit response must NOT leak the sealed material
    assert.equal(c.secret, undefined);
    assert.equal(c.salt, undefined);
    assert.equal(c.randomness, undefined);
    assert.deepEqual(c.derivation.sealed_until_reveal, ['randomness', 'secret', 'salt']);
    // …but the honesty block is published immediately
    assertHonest(c);

    // FREE reveal: no payment header, no 402, no facilitator round-trip
    const settlesBefore = t.fac.calls.settle.length;
    const r = await request(t.baseUrl, `/x402/random/reveal/${c.commit_id}`);
    assert.equal(r.status, 200);
    assert.equal(r.headers.get('payment-required'), null);
    assert.equal(t.fac.calls.settle.length, settlesBefore, 'reveal must never settle anything');
    assert.equal(t.fac.calls.verify.length, 1, 'reveal must never call the facilitator');
    assert.equal(r.json.free, true);
    assert.equal(r.json.commit_id, c.commit_id);
    assert.equal(r.json.commitment, c.commitment);
    assert.equal(r.json.memo, 'round-7');

    // rule 1: the commitment really opens
    const opened = Buffer.from(sha3_256(Buffer.concat([
      Buffer.from(r.json.secret, 'hex'), Buffer.from(r.json.salt, 'hex'),
    ]))).toString('hex');
    assert.equal(opened, c.commitment);

    // rule 2: the secret was not cherry-picked — it is the DRNG stream over
    // the (now disclosed) signed draw
    const rng = derive.makeRng(Buffer.from(r.json.randomness, 'hex'), 'commit', 'game-1');
    assert.equal(Buffer.from(rng.randbytes(32)).toString('hex'), r.json.secret);
    assert.equal(Buffer.from(rng.randbytes(32)).toString('hex'), r.json.salt);

    // rule 3: the node signed the disclosed draw
    assert.equal(r.json.randomness, ENTROPY_HEX);
    assert.equal(
      Buffer.from(sha3_256(Buffer.from(r.json.randomness, 'hex'))).toString('hex'),
      r.json.attestation.digest_hex);
    assertHonest(r.json);

    // idempotent: a second reveal is byte-identical (same revealed_at)
    const again = await request(t.baseUrl, `/x402/random/reveal/${c.commit_id}`);
    assert.equal(again.status, 200);
    assert.deepEqual(again.json, r.json);
  } finally {
    await t.close();
  }
});

test('random_commit: a sealed commitment stays sealed until reveal_after (425, no secret)', async () => {
  const t = await buildTestGateway();
  try {
    const { paid } = await post(t.baseUrl, '/x402/random/commit', { reveal_after_seconds: 3600 });
    const id = paid.json.commit_id;
    const early = await request(t.baseUrl, `/x402/random/reveal/${id}`);
    assert.equal(early.status, 425);
    assert.equal(early.json.error, 'too_early');
    assert.equal(early.json.secret, undefined);
    assert.equal(early.json.commitment, paid.json.commitment);
    assert.ok(early.json.seconds_remaining > 3500);
    assert.ok(Number(early.headers.get('retry-after')) > 3500);

    // unknown / malformed ids are a plain 404, never a payment demand
    const missing = await request(t.baseUrl, '/x402/random/reveal/rc_' + 'ab'.repeat(16));
    assert.equal(missing.status, 404);
    assert.equal(missing.json.error, 'commitment_not_found');
    const junk = await request(t.baseUrl, '/x402/random/reveal/not-a-commit');
    assert.equal(junk.status, 404);
    assert.equal(junk.headers.get('payment-required'), null);
  } finally {
    await t.close();
  }
});

test('random_commit: two commits never collide and caps refuse before payment', async () => {
  const t = await buildTestGateway();
  try {
    const a = await post(t.baseUrl, '/x402/random/commit', {}, { nonce: '0x' + crypto.randomBytes(32).toString('hex') });
    const b = await post(t.baseUrl, '/x402/random/commit', { request_id: 'other' });
    assert.notEqual(a.paid.json.commit_id, b.paid.json.commit_id);
    // same fixture bytes, different request_id => different secret/commitment
    assert.notEqual(a.paid.json.commitment, b.paid.json.commitment);

    for (const body of [
      { reveal_after_seconds: -1 },
      { reveal_after_seconds: 10 ** 9 },
      { memo: 'x'.repeat(300) },
      { memo: 42 },
    ]) {
      const res = await unpaidPost(t.baseUrl, '/x402/random/commit', body);
      assert.equal(res.status, 400, JSON.stringify(body).slice(0, 50));
      assert.equal(res.headers.get('payment-required'), null);
    }
  } finally {
    await t.close();
  }
});

// -------------------------------------------------------- shared fail-closed

test('random family: an unhealthy entropy source refuses ALL of them (503, never a 402)', async () => {
  const sick = JSON.parse(JSON.stringify(QRNG_FIXTURE));
  sick.health.passed = false;
  const t = await buildTestGateway({ handlers: chainHandlers({ qrng: sick }) });
  try {
    for (const [path, body] of [
      ['/x402/random/int', { min: 1, max: 6 }],
      ['/x402/random/shuffle', { n: 4 }],
      ['/x402/random/pick', { items: [1, 2] }],
      ['/x402/qrng/bulk', {}],
      ['/x402/random/commit', {}],
    ]) {
      const res = await unpaidPost(t.baseUrl, path, body);
      assert.equal(res.status, 503, path);
      assert.equal(res.headers.get('payment-required'), null, path);
    }
    const cat = await request(t.baseUrl, '/x402');
    for (const p of cat.json.products.filter((x) => x.id.startsWith('random_'))) {
      assert.equal(p.available, false, p.id);
      assert.equal(p.unavailable_reason, 'qrng_entropy_health_failed');
    }
    assert.equal(t.fac.calls.verify.length, 0);
    assert.equal(t.fac.calls.settle.length, 0);
  } finally {
    await t.close();
  }
});

test('random family: node unreachable refuses the whole family without charging', async () => {
  const t = await buildTestGateway({ handlers: { 'chain.getHead': () => ({ height: 10 }) } });
  try {
    const res = await unpaidPost(t.baseUrl, '/x402/random/int', { min: 1, max: 6 });
    assert.equal(res.status, 503);
    assert.equal(res.headers.get('payment-required'), null);
    assert.equal(t.fac.calls.settle.length, 0);
  } finally {
    await t.close();
  }
});

test('random family: X402_RANDOM_ENABLED=0 unroutes every route including the free reveal', async () => {
  const t = await buildTestGateway({ overrides: { randomEnabled: false } });
  try {
    const cat = await request(t.baseUrl, '/x402');
    assert.equal(cat.json.products.filter((p) => p.id.startsWith('random_')).length, 0);
    const res = await unpaidPost(t.baseUrl, '/x402/random/int', { min: 1, max: 6 });
    assert.equal(res.status, 404);
    const rev = await request(t.baseUrl, '/x402/random/reveal/rc_' + 'cd'.repeat(16));
    assert.equal(rev.status, 404);
    assert.equal(rev.json.error, 'not_found');
  } finally {
    await t.close();
  }
});

test('random family: prices are config-driven and idempotency never double-charges', async () => {
  const t = await buildTestGateway({ overrides: { randomIntPriceUsd: '0.03' } });
  try {
    const first = await unpaidPost(t.baseUrl, '/x402/random/int', { min: 1, max: 6 });
    assert.equal(protocol.decodeHeader(first.headers.get('payment-required')).accepts[0].amount, '30000');

    const nonce = '0x' + crypto.randomBytes(32).toString('hex');
    const body = JSON.stringify({ min: 1, max: 6, count: 3 });
    const opts = { method: 'POST', headers: { 'content-type': 'application/json' }, body, nonce, idemKey: 'idem-rand-1' };
    const a = await paidRequest(t.baseUrl, '/x402/random/int', opts);
    assert.equal(a.paid.status, 200);
    const b = await paidRequest(t.baseUrl, '/x402/random/int', opts);
    assert.equal(b.paid.status, 200);
    assert.equal(b.paid.headers.get('idempotent-replay'), 'true');
    assert.deepEqual(b.paid.json.result, a.paid.json.result);
    assert.equal(t.fac.calls.settle.length, 1, 'one payment, one settlement');
  } finally {
    await t.close();
  }
});
