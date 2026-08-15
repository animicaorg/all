'use strict';
/**
 * A. The randomness family — the hero products (X402-PRODUCTS-SPEC §A).
 *
 *   random_int      POST /x402/random/int       $0.01  uniform ints
 *   random_shuffle  POST /x402/random/shuffle   $0.02  Fisher-Yates permutation
 *   random_pick     POST /x402/random/pick      $0.02  weighted sample ±replacement
 *   random_bulk     POST /x402/qrng/bulk        $0.05  10 draws, one settlement
 *   random_commit   POST /x402/random/commit    $0.02  commit now…
 *                   GET  /x402/random/reveal/{id}  FREE  …reveal to everyone
 *
 * Three rules shape every line of this file:
 *
 * 1. ONE verified draw per request. Each product buys exactly one
 *    `rand.quantumRandomBytes` call through the shared source in qrng.js and
 *    then derives its whole answer from those bytes with the deterministic
 *    DRNG in derive.js. Never one node call per output item.
 *
 * 2. The buyer can recompute everything. Every response carries the raw
 *    `randomness` bytes (the commit product carries them at reveal time),
 *    the exact rule set (`derivation.rules`), the ordered `derivation.steps`
 *    and — whenever the repo's own `randomness/beacon_api/static/verify.js`
 *    reproduces the answer verbatim — a `derivation.recompute` object that
 *    can be dropped straight into `AnimicaBeacon.verifyResult()`. The
 *    derivations are byte-for-byte identical to that canonical verifier
 *    (cross-checked in test/random.test.js).
 *
 * 3. Honesty is not optional. `source`, `health`, `attestation` and
 *    `verification` ride on every randomness response verbatim from the
 *    node. Today they say `software-fallback` / `is_quantum: false` /
 *    `attested: false`, and so does every description here. Nothing in this
 *    file claims hardware or quantum anything.
 *
 * Caps are enforced in validate(), which the paywall runs BEFORE the
 * availability probe and BEFORE any 402 — an oversized or malformed request
 * is a 400 that was never sold. Readiness is the SAME health-gated probe the
 * qrng product uses, so a sick entropy source refuses the whole family at
 * 503 with no payment requested.
 */

const crypto = require('node:crypto');

const cfgMod = require('../config');
const { ProductError, ProductUnavailable } = require('./errors');
const derive = require('./derive');

const MAX_REQUEST_ID = 128;
const MAX_MEMO = 256;
const COMMIT_ID_RE = /^rc_[0-9a-f]{32}$/;
const REVEAL_PATH_RE = /^(?:\/x402)?\/random\/reveal\/([A-Za-z0-9_-]{1,80})$/;

function bad(detail, error = 'invalid_params', extra) {
  return new ProductError(detail, { body: Object.assign({ error, detail }, extra) });
}

/** POST body must be a JSON object; the paywall already capped its size. */
function jsonBody(ctx) {
  if (ctx.json === null || typeof ctx.json !== 'object' || Array.isArray(ctx.json)) {
    throw bad('send a JSON object body with content-type: application/json', 'invalid_body');
  }
  return ctx.json;
}

function intField(body, name, { min, max, fallback, required = false, aliases = [] }) {
  let raw = body[name];
  for (const a of aliases) if (raw === undefined) raw = body[a];
  if (raw === undefined || raw === null) {
    if (required) throw bad(`missing required field ${name}`);
    return fallback;
  }
  if (typeof raw !== 'number' || !Number.isInteger(raw)) {
    throw bad(`${name} must be an integer`);
  }
  if (raw < min || raw > max) {
    throw bad(`${name} must be an integer in [${min}, ${max}]`, 'invalid_params', { caps: { [name]: { min, max } } });
  }
  return raw;
}

function boolField(body, name, fallback) {
  const raw = body[name];
  if (raw === undefined || raw === null) return fallback;
  if (typeof raw !== 'boolean') throw bad(`${name} must be a boolean`);
  return raw;
}

/**
 * The caller's own tag. It is mixed into the DRNG seed, so a buyer can bind
 * a draw to their game round / order id and recompute it later.
 */
function requestIdField(body) {
  const raw = body.request_id;
  if (raw === undefined || raw === null) return '';
  if (typeof raw !== 'string' || raw.length > MAX_REQUEST_ID || !/^[\x20-\x7e]*$/.test(raw)) {
    throw bad(`request_id must be a printable ASCII string of at most ${MAX_REQUEST_ID} chars`);
  }
  return raw;
}

/** Items must be a JSON array of JSON values; only the COUNT is derived on. */
function itemsField(body, maxItems, { name = 'items' } = {}) {
  const items = body[name];
  if (!Array.isArray(items)) throw bad(`${name} must be an array`);
  if (items.length < 1) throw bad(`${name} must not be empty`);
  if (items.length > maxItems) {
    throw bad(`${name} has ${items.length} entries; max ${maxItems} per request`, 'too_many_items',
      { caps: { max_items: maxItems } });
  }
  return items;
}

/**
 * Weights are non-negative INTEGERS on purpose: the buyer has to recompute
 * the cumulative-weight search exactly, and binary floating point does not
 * survive that trip across languages. 2^32-1 per entry keeps the total below
 * 2^53 for the maximum item count, so the totals stay exact.
 */
function weightsField(body, n) {
  const w = body.weights;
  if (w === undefined || w === null) return null;
  if (!Array.isArray(w)) throw bad('weights must be an array of non-negative integers');
  if (w.length !== n) throw bad(`weights must have exactly one entry per item (got ${w.length}, items ${n})`);
  let total = 0;
  for (const x of w) {
    if (typeof x !== 'number' || !Number.isInteger(x) || x < 0 || x > 4294967295) {
      throw bad('every weight must be an integer in [0, 4294967295] — floats are rejected because the buyer must recompute the cumulative search exactly');
    }
    total += x;
  }
  if (total <= 0) throw bad('the weights must sum to more than 0');
  return { weights: w, total, positive: w.filter((x) => x > 0).length };
}

/** hex-encoded segment [start, end) of a hex string. */
function hexSlice(hex, startByte, lenBytes) {
  return hex.slice(startByte * 2, (startByte + lenBytes) * 2);
}

function createRandomProducts({ cfg, node, source, gatewayStore, now = Date.now }) {
  const src = source;
  const { fetchRandom, attachHonesty } = src;
  const nowSec = () => Math.floor(now() / 1000);

  /** Shared readiness: the same health-gated probe qrng uses. */
  function availability() {
    return src.probe();
  }

  /**
   * Shared response skeleton: the friendly answer, the raw bytes it came
   * from, the node's honesty fields verbatim, then the derivation block.
   */
  function baseBody(productId, result, resultObj) {
    const body = attachHonesty({
      product: productId,
      result: resultObj,
      randomness: result.bytes_hex,
      encoding: 'hex',
      bytes: result.n,
    }, result);
    return body;
  }

  /**
   * The derivation block. `recompute` is present ONLY when the repo's
   * verify.js reproduces `output` verbatim from (kind, entropy, request_id,
   * params) — otherwise the ordered `steps` are the recipe, and they use the
   * same primitives.
   */
  function derivationBlock({ algorithm, kind, requestId, entropyHex, seedHex, steps, rules, recompute, bytesConsumed, extra }) {
    const block = {
      algorithm,
      kind,
      request_id: requestId,
      domain: derive.DOMAIN,
      seed_hex: seedHex,
      entropy_hex: entropyHex,
      rules: Object.assign({
        seed: derive.RULES.seed,
        stream: derive.RULES.stream,
        uniform_int: derive.RULES.uniform_int,
      }, rules || {}),
      steps,
      stream_bytes_consumed: bytesConsumed,
      recompute: recompute || null,
      verifier: derive.VERIFIER,
    };
    if (extra) Object.assign(block, extra);
    return block;
  }

  // ------------------------------------------------------------ random_int

  const randomInt = {
    id: 'random_int',
    title: 'Uniform random integers',
    description:
      `Uniform integers in [min, max] derived from ONE verified randomness draw by rejection sampling (never modulo bias) — up to ${cfg.randomMaxInts} per request. The response carries the raw draw, the node's source/health/attestation and the exact rule, so you can recompute every integer offline.`,
    path: '/x402/random/int',
    routes: [{ method: 'POST', path: '/x402/random/int' }],
    priceUsd: cfg.randomIntPriceUsd,
    enabled: cfg.randomEnabled,
    mode: 'execute-then-settle',
    mimeType: 'application/json',
    maxBodyBytes: 16 * 1024,
    outputSchema: {
      input: {
        type: 'http',
        method: 'POST',
        bodyType: 'json',
        bodyFields: {
          min: { type: 'integer', required: true, description: 'lower bound, inclusive' },
          max: { type: 'integer', required: true, description: 'upper bound, inclusive (>= min)' },
          count: { type: 'integer', required: false, description: `how many integers, 1..${cfg.randomMaxInts} (default 1; alias n)` },
          request_id: { type: 'string', required: false, description: 'your own tag; mixed into the DRNG seed so you can recompute this exact draw' },
        },
      },
      output: {
        type: 'json',
        description:
          'result {ints}, randomness (hex raw draw), source, health, attestation, verification, derivation {rules, steps, recompute} for offline recomputation, payment metadata',
      },
    },
    availability,
    validate(ctx) {
      const body = jsonBody(ctx);
      const min = intField(body, 'min', { min: -Number.MAX_SAFE_INTEGER, max: Number.MAX_SAFE_INTEGER, required: true });
      const max = intField(body, 'max', { min: -Number.MAX_SAFE_INTEGER, max: Number.MAX_SAFE_INTEGER, required: true });
      if (max < min) throw bad('max must be >= min');
      const range = max - min + 1;
      if (!Number.isSafeInteger(range)) throw bad('the range max-min+1 must be a safe integer (< 2^53)');
      const count = intField(body, 'count', { min: 1, max: cfg.randomMaxInts, fallback: 1, aliases: ['n'] });
      return { min, max, count, requestId: requestIdField(body) };
    },
    async handler(ctx) {
      const { min, max, count, requestId } = ctx.params;
      const result = await fetchRandom(cfg.randomSeedBytes);
      const entropy = derive.hexToBytes(result.bytes_hex);
      const d = derive.uniformInts({ entropy, requestId, lo: min, hi: max, count });
      const bodyObj = baseBody('random_int', result, { ints: d.output, count, min, max });
      bodyObj.derivation = derivationBlock({
        algorithm: 'uniform-int-rejection-sampling',
        kind: d.kind,
        requestId,
        entropyHex: result.bytes_hex,
        seedHex: derive.bytesToHex(derive.drngSeed(entropy, d.kind, requestId)),
        steps: [
          `seed the DRNG with kind="${d.kind}" and your request_id over the raw randomness above`,
          `for i = 0..${count - 1}: ints[i] = ${min} + randbelow(${max - min + 1})`,
        ],
        bytesConsumed: d.rng.bytesConsumed,
        // verify.js compute("range", …) reproduces this array verbatim.
        recompute: { kind: 'range', beacon_hex: result.bytes_hex, round_id: 0, request_id: requestId, params: d.params, output: d.output },
      });
      return { status: 200, bodyObj };
    },
  };

  // -------------------------------------------------------- random_shuffle

  const randomShuffle = {
    id: 'random_shuffle',
    title: 'Verifiable shuffle',
    description:
      `Fisher-Yates permutation of your list (or of 1..N), up to ${cfg.randomMaxItems} items, derived from ONE verified randomness draw. Returns the permutation of 0-based indices plus the shuffled items, with the exact swap-and-byte-consumption rule so you can recompute it offline.`,
    path: '/x402/random/shuffle',
    routes: [{ method: 'POST', path: '/x402/random/shuffle' }],
    priceUsd: cfg.randomShufflePriceUsd,
    enabled: cfg.randomEnabled,
    mode: 'execute-then-settle',
    mimeType: 'application/json',
    maxBodyBytes: cfg.randomMaxBodyBytes,
    outputSchema: {
      input: {
        type: 'http',
        method: 'POST',
        bodyType: 'json',
        bodyFields: {
          items: { type: 'array', required: false, description: `the list to shuffle, 1..${cfg.randomMaxItems} entries (any JSON values)` },
          n: { type: 'integer', required: false, description: `shuffle 1..n instead of a supplied list (1..${cfg.randomMaxItems}); supply exactly one of items or n` },
          request_id: { type: 'string', required: false, description: 'your own tag; mixed into the DRNG seed' },
        },
      },
      output: {
        type: 'json',
        description:
          'result {permutation (0-based indices), items (shuffled), count}, randomness, source, health, attestation, verification, derivation {rules, steps, recompute}, payment metadata',
      },
    },
    availability,
    validate(ctx) {
      const body = jsonBody(ctx);
      const hasItems = body.items !== undefined && body.items !== null;
      const hasN = body.n !== undefined && body.n !== null;
      if (hasItems === hasN) throw bad('supply exactly one of items (array) or n (integer)');
      let items = null;
      let n;
      if (hasItems) {
        items = itemsField(body, cfg.randomMaxItems);
        n = items.length;
      } else {
        n = intField(body, 'n', { min: 1, max: cfg.randomMaxItems, required: true });
      }
      return { items, n, requestId: requestIdField(body) };
    },
    async handler(ctx) {
      const { items, n, requestId } = ctx.params;
      const result = await fetchRandom(cfg.randomSeedBytes);
      const entropy = derive.hexToBytes(result.bytes_hex);
      const d = derive.shuffleIndices({ entropy, requestId, n });
      const perm = d.output;
      // The index list is what was permuted; applying it to the caller's
      // items is the same swap sequence as shuffling the items directly.
      const shuffled = items ? perm.map((i) => items[i]) : perm.map((i) => i + 1);
      const bodyObj = baseBody('random_shuffle', result, { count: n, permutation: perm, items: shuffled });
      bodyObj.derivation = derivationBlock({
        algorithm: 'fisher-yates-permutation',
        kind: d.kind,
        requestId,
        entropyHex: result.bytes_hex,
        seedHex: derive.bytesToHex(derive.drngSeed(entropy, d.kind, requestId)),
        rules: { shuffle: derive.RULES.shuffle },
        steps: [
          `a = [0, 1, …, ${n - 1}]`,
          `for i = ${n - 1} down to 1: j = randbelow(i+1); swap a[i], a[j]`,
          items
            ? 'result.items[i] = your items[permutation[i]]'
            : 'result.items[i] = permutation[i] + 1 (the 1..n form)',
        ],
        bytesConsumed: d.rng.bytesConsumed,
        // verify.js compute("shuffle", …, {items: [0..n-1]}) reproduces this.
        recompute: {
          kind: 'shuffle',
          beacon_hex: result.bytes_hex,
          round_id: 0,
          request_id: requestId,
          params: { items: Array.from({ length: n }, (_, i) => i) },
          output: perm,
        },
      });
      return { status: 200, bodyObj };
    },
  };

  // ----------------------------------------------------------- random_pick

  const randomPick = {
    id: 'random_pick',
    title: 'Weighted random pick',
    description:
      `Pick k of your items with or without replacement, optionally weighted by integer weights (raffles, lotteries, sortition, A/B splits). One verified randomness draw, cumulative-weight search over a rejection-sampled uniform value, up to ${cfg.randomMaxItems} items. The exact rule and the raw draw ride along so you can recompute the winners.`,
    path: '/x402/random/pick',
    routes: [{ method: 'POST', path: '/x402/random/pick' }],
    priceUsd: cfg.randomPickPriceUsd,
    enabled: cfg.randomEnabled,
    mode: 'execute-then-settle',
    mimeType: 'application/json',
    maxBodyBytes: cfg.randomMaxBodyBytes,
    outputSchema: {
      input: {
        type: 'http',
        method: 'POST',
        bodyType: 'json',
        bodyFields: {
          items: { type: 'array', required: true, description: `candidates, 1..${cfg.randomMaxItems} entries (any JSON values)` },
          k: { type: 'integer', required: false, description: `how many to pick, 1..${cfg.randomMaxPicks} (default 1; alias count). Without replacement k <= items.length` },
          replace: { type: 'boolean', required: false, description: 'true = with replacement (an item can win twice); default false' },
          weights: { type: 'array', required: false, description: 'one non-negative INTEGER weight per item (floats rejected: the cumulative search must be recomputable exactly)' },
          request_id: { type: 'string', required: false, description: 'your own tag; mixed into the DRNG seed' },
        },
      },
      output: {
        type: 'json',
        description:
          'result {picked, indices, k, replace, weighted}, randomness, source, health, attestation, verification, derivation {rules, steps, recompute?}, payment metadata',
      },
    },
    availability,
    validate(ctx) {
      const body = jsonBody(ctx);
      const items = itemsField(body, cfg.randomMaxItems);
      const k = intField(body, 'k', { min: 1, max: cfg.randomMaxPicks, fallback: 1, aliases: ['count'] });
      const replace = boolField(body, 'replace', false);
      const w = weightsField(body, items.length);
      if (!replace && k > items.length) {
        throw bad(`k=${k} exceeds items.length=${items.length}; use replace: true to allow repeats`);
      }
      if (!replace && w && k > w.positive) {
        throw bad(`k=${k} exceeds the ${w.positive} item(s) with a weight greater than 0; without replacement every pick needs a positive weight`);
      }
      return { items, k, replace, weights: w ? w.weights : null, requestId: requestIdField(body) };
    },
    async handler(ctx) {
      const { items, k, replace, weights, requestId } = ctx.params;
      const n = items.length;
      const result = await fetchRandom(cfg.randomSeedBytes);
      const entropy = derive.hexToBytes(result.bytes_hex);

      let d;
      let algorithm;
      let steps;
      let rules;
      let recompute = null;
      const idxParams = () => Array.from({ length: n }, (_, i) => i);

      if (!weights && !replace) {
        d = derive.sampleIndices({ entropy, requestId, n, k });
        algorithm = 'partial-fisher-yates-sample';
        rules = { sample: derive.RULES.sample_without_replacement };
        steps = [
          `a = [0, 1, …, ${n - 1}]`,
          `for i = 0..${k - 1}: j = i + randbelow(${n}-i); swap a[i], a[j]; indices[i] = a[i]`,
          'result.picked[i] = your items[indices[i]]',
        ];
        // verify.js compute("lottery", …, {entries: [0..n-1], k}) matches.
        recompute = { kind: 'lottery', beacon_hex: result.bytes_hex, round_id: 0, request_id: requestId, params: { entries: idxParams(), k }, output: d.output };
      } else if (!weights && replace) {
        d = derive.uniformIndices({ entropy, requestId, n, k });
        algorithm = 'uniform-index-with-replacement';
        rules = { sample: derive.RULES.sample_with_replacement };
        steps = [`for i = 0..${k - 1}: indices[i] = randbelow(${n})`, 'result.picked[i] = your items[indices[i]]'];
        if (k === 1) {
          // Exactly verify.js compute("choice", …) — a single index.
          recompute = { kind: 'choice', beacon_hex: result.bytes_hex, round_id: 0, request_id: requestId, params: { items: idxParams() }, output: d.output[0] };
        }
      } else {
        d = derive.weightedIndices({ entropy, requestId, weights, k, replace });
        algorithm = replace ? 'weighted-cumulative-with-replacement' : 'weighted-cumulative-without-replacement';
        rules = { weighted: derive.RULES.weighted };
        steps = replace
          ? [
            `total = sum(weights) = ${weights.reduce((a, b) => a + b, 0)}`,
            `for i = 0..${k - 1}: r = randbelow(total); indices[i] = smallest j with r < weights[0]+…+weights[j]`,
            'result.picked[i] = your items[indices[i]]',
          ]
          : [
            'remaining = your weights, in the original order',
            `for i = 0..${k - 1}: total = sum(remaining); r = randbelow(total); pick the smallest j with r < remaining[0]+…+remaining[j]; emit its ORIGINAL index; remove entry j from remaining`,
            'result.picked[i] = your items[indices[i]]',
          ];
        if (k === 1) {
          // The first weighted draw is exactly verify.js compute("weighted", …).
          recompute = { kind: 'weighted', beacon_hex: result.bytes_hex, round_id: 0, request_id: requestId, params: { items: idxParams(), weights }, output: d.output[0] };
        }
      }

      const indices = d.output;
      const bodyObj = baseBody('random_pick', result, {
        picked: indices.map((i) => items[i]),
        indices,
        k,
        replace,
        weighted: Boolean(weights),
      });
      bodyObj.derivation = derivationBlock({
        algorithm,
        kind: d.kind,
        requestId,
        entropyHex: result.bytes_hex,
        seedHex: derive.bytesToHex(derive.drngSeed(entropy, d.kind, requestId)),
        rules,
        steps,
        bytesConsumed: d.rng.bytesConsumed,
        recompute,
      });
      if (!recompute) {
        bodyObj.derivation.recompute_note =
          'verify.js compute() has no single kind for this multi-draw variant — follow `steps` with AnimicaBeacon.QDRNG(AnimicaBeacon.drngSeed(entropy, kind, request_id)); each primitive is the stock one.';
      }
      return { status: 200, bodyObj };
    },
  };

  // ---------------------------------------------------------- random_bulk

  const randomBulk = {
    id: 'random_bulk',
    title: 'Bulk randomness (10 draws, one settlement)',
    description:
      `Up to ${cfg.randomBulkMaxDraws} independent random segments of up to ${cfg.qrngMaxBytes} bytes each, delivered in ONE payment — ${cfg.randomBulkMaxDraws} segments for $${cfg.randomBulkPriceUsd} against $${cfg.qrngPriceUsd} per single draw, a real per-unit discount. The segments are contiguous, non-overlapping slices of one attested draw (the attestation covers the whole concatenation) — stated plainly, not marketed as separate node calls.`,
    path: '/x402/qrng/bulk',
    routes: [{ method: 'POST', path: '/x402/qrng/bulk' }],
    priceUsd: cfg.randomBulkPriceUsd,
    enabled: cfg.randomEnabled,
    mode: 'execute-then-settle',
    mimeType: 'application/json',
    maxBodyBytes: 16 * 1024,
    outputSchema: {
      input: {
        type: 'http',
        method: 'POST',
        bodyType: 'json',
        bodyFields: {
          draws: { type: 'integer', required: false, description: `how many segments, 1..${cfg.randomBulkMaxDraws} (default ${cfg.randomBulkMaxDraws}; alias count)` },
          bytes: { type: 'integer', required: false, description: `bytes per segment, 1..${cfg.qrngMaxBytes} (default 32; draws*bytes <= ${cfg.randomMaxDrawBytes})` },
        },
      },
      output: {
        type: 'json',
        description:
          'result {draws: [{index, offset, bytes, randomness, sha3_256}], count, bytes_per_draw, pricing}, randomness (the full concatenation), source, health, attestation, verification, derivation (the split rule), payment metadata',
      },
    },
    availability,
    validate(ctx) {
      const body = jsonBody(ctx);
      const draws = intField(body, 'draws', { min: 1, max: cfg.randomBulkMaxDraws, fallback: cfg.randomBulkMaxDraws, aliases: ['count'] });
      const bytes = intField(body, 'bytes', { min: 1, max: cfg.qrngMaxBytes, fallback: 32, aliases: ['n'] });
      const total = draws * bytes;
      if (total > cfg.randomMaxDrawBytes) {
        throw bad(`draws*bytes = ${total} exceeds the ${cfg.randomMaxDrawBytes}-byte cap for one request`, 'request_too_large',
          { caps: { max_total_bytes: cfg.randomMaxDrawBytes, max_draws: cfg.randomBulkMaxDraws, max_bytes_per_draw: cfg.qrngMaxBytes } });
      }
      return { draws, bytes, total };
    },
    async handler(ctx) {
      const { draws, bytes, total } = ctx.params;
      const result = await fetchRandom(total);
      const hex = result.bytes_hex;
      if (hex.length !== total * 2) {
        throw new ProductUnavailable('qrng_short_draw',
          `randomness RPC returned ${hex.length / 2} bytes, expected ${total}`);
      }
      const segments = [];
      for (let i = 0; i < draws; i++) {
        const offset = i * bytes;
        const seg = hexSlice(hex, offset, bytes);
        segments.push({ index: i, offset, bytes, randomness: seg, sha3_256: derive.sha3Hex(derive.hexToBytes(seg)) });
      }
      // Priced from config, not from the registry-injected field: the
      // per-unit discount is a claim in the response body and must hold even
      // when this product is constructed outside the registry.
      const priceAtomic = BigInt(cfgMod.usdToUsdcAtomic(cfg.randomBulkPriceUsd));
      const perDraw = priceAtomic / BigInt(draws);
      const bodyObj = baseBody('random_bulk', result, {
        count: draws,
        bytes_per_draw: bytes,
        draws: segments,
        pricing: {
          price_usd: cfg.randomBulkPriceUsd,
          price_atomic: priceAtomic.toString(),
          price_atomic_per_draw: perDraw.toString(),
          single_draw_price_usd: cfg.qrngPriceUsd,
          note: `one settlement for ${draws} segment(s); the single-draw product charges $${cfg.qrngPriceUsd} per call`,
        },
      });
      bodyObj.derivation = {
        algorithm: 'segment-split',
        rule: 'draws[i].randomness == randomness[i*bytes_per_draw : (i+1)*bytes_per_draw] (byte offsets; the hex string is sliced at 2*offset)',
        per_segment_digest: 'draws[i].sha3_256 == sha3_256(bytes(draws[i].randomness))',
        honesty:
          'these segments come from ONE attested draw of the full concatenation, not from separate node calls; attestation.digest_hex covers `randomness` in full, and each segment inherits that single signature',
        verifier: derive.VERIFIER,
      };
      return { status: 200, bodyObj };
    },
  };

  // --------------------------------------------------------- random_commit

  const COMMIT_ALGORITHM = 'sha3_256(secret||salt)';
  const COMMIT_KIND = 'commit';

  function commitDerivationRules(requestId) {
    return {
      secret_and_salt:
        `seed the DRNG with kind="${COMMIT_KIND}" and request_id=${JSON.stringify(requestId)} over the revealed randomness, then read 64 stream bytes: secret = bytes[0:32], salt = bytes[32:64]`,
      commitment: 'commitment == sha3_256(bytes(secret) || bytes(salt))',
      draw: 'attestation.digest_hex == sha3_256(bytes(randomness)) — the node signed the draw the secret came from',
      seed: derive.RULES.seed,
      stream: derive.RULES.stream,
    };
  }

  const randomCommit = {
    id: 'random_commit',
    title: 'Commit-reveal randomness (provably fair)',
    description:
      `Commit now, reveal to everyone later: the paid call returns a commitment sha3_256(secret||salt) and a commit id; GET /x402/random/reveal/{commit_id} is FREE, public and idempotent, and discloses the secret, the salt, the raw draw and the node's attestation so anyone — not just the buyer — can check the commitment. Reveal delay 0..${cfg.randomCommitMaxDelaySec}s. Built for provably-fair games, sealed-bid rounds and auditable draws.`,
    path: '/x402/random/commit',
    routes: [{ method: 'POST', path: '/x402/random/commit' }],
    priceUsd: cfg.randomCommitPriceUsd,
    enabled: cfg.randomEnabled,
    mode: 'execute-then-settle',
    mimeType: 'application/json',
    maxBodyBytes: 16 * 1024,
    outputSchema: {
      input: {
        type: 'http',
        method: 'POST',
        bodyType: 'json',
        bodyFields: {
          reveal_after_seconds: { type: 'integer', required: false, description: `seconds until the free reveal opens, 0..${cfg.randomCommitMaxDelaySec} (default 0 = revealable immediately)` },
          memo: { type: 'string', required: false, description: `up to ${MAX_MEMO} printable ASCII chars, published verbatim at reveal (bind the commitment to your round/bet id)` },
          request_id: { type: 'string', required: false, description: 'your own tag; mixed into the DRNG seed that produced the secret' },
        },
      },
      output: {
        type: 'json',
        description:
          'commit_id, commitment, algorithm, reveal_after (unix seconds), reveal_url, source, health, attestation (the draw is NOT disclosed yet), verification, payment metadata',
      },
    },
    /** The free, public, unpaid reveal — the whole point of the product. */
    freeRoutes: [{
      method: 'GET',
      path: '/x402/random/reveal/{commit_id}',
      description: 'FREE public reveal: secret, salt, raw draw and attestation for a commitment. No payment, ever. Idempotent.',
      match(pathname) {
        const m = REVEAL_PATH_RE.exec(pathname);
        return m ? { commit_id: m[1] } : null;
      },
      handler(ctx) {
        const id = ctx.params.commit_id;
        if (!COMMIT_ID_RE.test(id)) {
          return { status: 404, bodyObj: { error: 'commitment_not_found', detail: 'no commitment with that id' } };
        }
        const row = gatewayStore.getCommitment(id);
        if (!row) {
          return {
            status: 404,
            bodyObj: {
              error: 'commitment_not_found',
              detail: `no commitment with id ${id} (commitments are pruned after ${cfg.randomCommitTtlSeconds}s)`,
            },
          };
        }
        const t = nowSec();
        if (t < row.reveal_after) {
          // Sealed until its own deadline: disclosing early would defeat the
          // commitment. No payment can change this — it is a clock, not a gate.
          return {
            status: 425,
            headers: { 'retry-after': String(Math.max(1, row.reveal_after - t)) },
            bodyObj: {
              error: 'too_early',
              detail: 'this commitment is still sealed',
              commit_id: row.commit_id,
              commitment: row.commitment,
              algorithm: row.algorithm,
              reveal_after: row.reveal_after,
              seconds_remaining: row.reveal_after - t,
            },
          };
        }
        // First disclosure stamps revealed_at; later reads are identical.
        gatewayStore.markCommitmentRevealed(id, t);
        const fresh = gatewayStore.getCommitment(id) || row;
        const draw = JSON.parse(fresh.draw_json);
        const bodyObj = {
          product: 'random_commit',
          stage: 'reveal',
          free: true,
          commit_id: fresh.commit_id,
          commitment: fresh.commitment,
          algorithm: fresh.algorithm,
          secret: fresh.secret_hex,
          salt: fresh.salt_hex,
          encoding: 'hex',
          memo: fresh.memo,
          request_id: fresh.request_id,
          created_at: fresh.created_at,
          reveal_after: fresh.reveal_after,
          revealed_at: fresh.revealed_at === null ? t : fresh.revealed_at,
        };
        bodyObj.randomness = draw.bytes_hex;
        bodyObj.bytes = draw.n;
        if (draw.source !== undefined) bodyObj.source = draw.source;
        if (draw.health !== undefined) bodyObj.health = draw.health;
        if (draw.attestation !== undefined) bodyObj.attestation = draw.attestation;
        bodyObj.verification = src.buildVerification(draw);
        bodyObj.derivation = {
          algorithm: 'commit-reveal',
          kind: COMMIT_KIND,
          request_id: fresh.request_id || '',
          domain: derive.DOMAIN,
          rules: commitDerivationRules(fresh.request_id || ''),
          steps: [
            'check commitment == sha3_256(bytes(secret) || bytes(salt)) — this is the commit-reveal property',
            'check the secret was not cherry-picked: rebuild the DRNG from `randomness` with kind="commit" and your request_id, read 64 bytes, and compare with secret||salt',
            'check the node signed the draw: attestation.digest_hex == sha3_256(bytes(randomness)) and ed25519_verify(public_key_hex, raw digest bytes, signature_hex)',
          ],
          verifier: derive.VERIFIER,
        };
        return { status: 200, bodyObj };
      },
    }],
    availability,
    validate(ctx) {
      const body = jsonBody(ctx);
      const delay = intField(body, 'reveal_after_seconds', { min: 0, max: cfg.randomCommitMaxDelaySec, fallback: 0, aliases: ['delay_seconds'] });
      let memo = body.memo;
      if (memo === undefined || memo === null) {
        memo = null;
      } else if (typeof memo !== 'string' || memo.length > MAX_MEMO || !/^[\x20-\x7e]*$/.test(memo)) {
        throw bad(`memo must be a printable ASCII string of at most ${MAX_MEMO} chars`);
      }
      return { delay, memo, requestId: requestIdField(body) };
    },
    async handler(ctx) {
      const { delay, memo, requestId } = ctx.params;
      const result = await fetchRandom(cfg.randomSeedBytes);
      const entropy = derive.hexToBytes(result.bytes_hex);
      const rng = derive.makeRng(entropy, COMMIT_KIND, requestId);
      const secret = Buffer.from(rng.randbytes(32));
      const salt = Buffer.from(rng.randbytes(32));
      const commitment = derive.sha3Hex(Buffer.concat([secret, salt]));
      const commitId = 'rc_' + crypto.randomBytes(16).toString('hex');
      const createdAt = nowSec();
      const revealAfter = createdAt + delay;

      // Sealed material lands in the gateway DB, never in this response.
      gatewayStore.putCommitment({
        commitId,
        commitment,
        algorithm: COMMIT_ALGORITHM,
        kind: COMMIT_KIND,
        requestId,
        memo,
        secretHex: secret.toString('hex'),
        saltHex: salt.toString('hex'),
        draw: result,
        revealAfter,
        createdAt,
      });

      const bodyObj = {
        product: 'random_commit',
        stage: 'commit',
        commit_id: commitId,
        commitment,
        algorithm: COMMIT_ALGORITHM,
        encoding: 'hex',
        reveal_after: revealAfter,
        reveal_after_seconds: delay,
        reveal_url: `${cfg.resourceBaseUrl.replace(/\/$/, '')}/x402/random/reveal/${commitId}`,
        reveal_is_free: true,
        memo,
        request_id: requestId,
        created_at: createdAt,
        bytes: result.n,
      };
      // Honesty fields for the draw are published NOW; the draw itself
      // (`randomness`) stays sealed until the reveal. attestation.digest_hex
      // is a second, independent commitment to the same bytes — publishing it
      // early is what lets anyone check later that nothing was swapped.
      if (result.source !== undefined) bodyObj.source = result.source;
      if (result.health !== undefined) bodyObj.health = result.health;
      if (result.attestation !== undefined) bodyObj.attestation = result.attestation;
      bodyObj.verification = src.buildVerification(result);
      bodyObj.derivation = {
        algorithm: 'commit-reveal',
        kind: COMMIT_KIND,
        request_id: requestId,
        domain: derive.DOMAIN,
        rules: commitDerivationRules(requestId),
        sealed_until_reveal: ['randomness', 'secret', 'salt'],
        steps: [
          'keep this commitment (publish it to your players before the round)',
          `GET ${bodyObj.reveal_url} once reveal_after has passed — it is free, public and idempotent`,
          'anyone can then check commitment == sha3_256(secret||salt) and that secret||salt came from the signed draw',
        ],
        verifier: derive.VERIFIER,
      };
      return { status: 200, bodyObj };
    },
  };

  return [randomInt, randomShuffle, randomPick, randomBulk, randomCommit];
}

module.exports = { createRandomProducts, REVEAL_PATH_RE, COMMIT_ID_RE };
