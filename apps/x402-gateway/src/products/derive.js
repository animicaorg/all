'use strict';
/**
 * Deterministic, documented derivations over ONE verified randomness draw.
 *
 * Every randomness product (int, shuffle, pick, bulk, commit) buys exactly
 * one draw from the node (src/products/qrng.js) and then derives its answer
 * here. Nothing in this file talks to the network, and nothing here is
 * invented: it is a byte-for-byte mirror of the canonical Animica DRNG in
 * `randomness/qrng/public.py` (QDRNG) and its dependency-free JS twin
 * `randomness/beacon_api/static/verify.js` (`AnimicaBeacon`), which the repo
 * already ships and which anyone can run offline:
 *
 *   seed   = sha3_256("animica/qrng/public/v1" || "|k:" || kind ||
 *                     "|r:" || request_id || "|b:" || entropy_bytes)
 *   stream = sha3_256(seed || be8(0)) || sha3_256(seed || be8(1)) || …
 *            consumed left to right, 32 bytes per block, never reused
 *   randbelow(n):                                 [rejection sampling]
 *            k = floor((bitlen(n) + 7) / 8) + 1
 *            limit = 2^(8k);  threshold = limit - (limit mod n)
 *            repeat: x = big-endian uint of the NEXT k stream bytes
 *                    (a rejected attempt still consumes its k bytes)
 *            until x < threshold;  result = x mod n
 *            n <= 1 consumes nothing and returns 0.
 *
 * `randbelow` is rejection sampling on purpose: `x mod n` alone is biased
 * towards small values whenever n does not divide 2^(8k), and a lottery
 * running on a biased mapping is not a lottery. The threshold cut removes
 * that bias exactly.
 *
 * Derived primitives, all consuming the SAME stream in the SAME order:
 *   shuffle(N)      Fisher-Yates over indices [0..N-1]:
 *                     for i = N-1 down to 1: j = randbelow(i+1); swap(i, j)
 *   sample(N, k)    partial Fisher-Yates (k draws WITHOUT replacement):
 *                     for i = 0..k-1: j = i + randbelow(N-i); swap(i, j);
 *                                     emit a[i]
 *   weighted(w)     cumulative-weight search over ONE uniform draw:
 *                     total = sum(w); r = randbelow(total);
 *                     smallest i with r < w[0]+…+w[i]
 *
 * The `kind` string is part of the seed, so two products cannot accidentally
 * derive the same numbers from the same bytes.
 */

const { sha3_256 } = require('@noble/hashes/sha3.js');

const DOMAIN = 'animica/qrng/public/v1';
const DOMAIN_BYTES = Buffer.from(DOMAIN, 'utf8');

/** verify.js `be8`: 8-byte big-endian counter. */
function be8(num) {
  const out = Buffer.alloc(8);
  let v = num;
  for (let i = 7; i >= 0; i--) {
    out[i] = v & 0xff;
    v = Math.floor(v / 256);
  }
  return out;
}

function hexToBytes(hex) {
  const h = String(hex || '').replace(/^0x/, '');
  if (h.length % 2 !== 0 || /[^0-9a-fA-F]/.test(h)) throw new Error('invalid hex string');
  return Buffer.from(h, 'hex');
}

function bytesToHex(b) {
  return Buffer.from(b).toString('hex');
}

function sha3Hex(buf) {
  return bytesToHex(sha3_256(Buffer.from(buf)));
}

/** verify.js `bitLength` (exact, Number-domain — n stays < 2^53 by cap). */
function bitLength(n) {
  let b = 0;
  let v = n;
  while (v > 0) {
    b += 1;
    v = Math.floor(v / 2);
  }
  return b;
}

/**
 * seed = sha3_256(DOMAIN || "|k:" || kind || "|r:" || request_id || "|b:" || entropy)
 * (mirrors verify.js `drngSeed`; `entropy` there is called `beacon`).
 */
function drngSeed(entropy, kind, requestId) {
  return Buffer.from(sha3_256(Buffer.concat([
    DOMAIN_BYTES,
    Buffer.from('|k:', 'utf8'), Buffer.from(String(kind), 'utf8'),
    Buffer.from('|r:', 'utf8'), Buffer.from(String(requestId), 'utf8'),
    Buffer.from('|b:', 'utf8'), Buffer.from(entropy),
  ])));
}

/** The counter-mode SHA3 stream + rejection-sampled integers. */
class QDRNG {
  constructor(seed) {
    this.seed = Buffer.from(seed);
    this.ctr = 0;
    this.buf = Buffer.alloc(0);
    this.bytesConsumed = 0; // audit counter (reported in the derivation block)
  }

  _refill() {
    this.buf = Buffer.concat([this.buf, Buffer.from(sha3_256(Buffer.concat([this.seed, be8(this.ctr)])))]);
    this.ctr += 1;
  }

  randbytes(n) {
    while (this.buf.length < n) this._refill();
    const out = this.buf.subarray(0, n);
    this.buf = this.buf.subarray(n);
    this.bytesConsumed += n;
    return out;
  }

  /** Uniform in [0, n) by rejection sampling — never `x mod n` alone. */
  randbelow(n) {
    if (n <= 1) return 0;
    const nb = BigInt(n);
    const k = Math.floor((bitLength(n) + 7) / 8) + 1;
    const limit = 1n << BigInt(8 * k);
    const threshold = limit - (limit % nb);
    for (;;) {
      const xb = this.randbytes(k);
      let x = 0n;
      for (let i = 0; i < xb.length; i++) x = (x << 8n) | BigInt(xb[i]);
      if (x < threshold) return Number(x % nb);
    }
  }

  randrange(lo, hi) {
    return lo + this.randbelow(hi - lo + 1);
  }
}

function makeRng(entropy, kind, requestId) {
  return new QDRNG(drngSeed(entropy, kind, requestId));
}

/**
 * `count` uniform ints in [lo, hi], drawn in order.
 * Identical to verify.js `compute("range", entropy, round, request_id,
 * {lo, hi, count})`.
 */
function uniformInts({ entropy, requestId, lo, hi, count }) {
  const rng = makeRng(entropy, 'range', requestId);
  const output = [];
  for (let i = 0; i < count; i++) output.push(rng.randrange(lo, hi));
  return { kind: 'range', params: { lo, hi, count }, output, rng };
}

/**
 * Fisher-Yates permutation of the index list [0..n-1].
 * Identical to verify.js `compute("shuffle", …, {items: [0..n-1]})`.
 * Shuffling the index list and applying it is the same swap sequence as
 * shuffling the items directly, and keeps the verifiable object small and
 * independent of how big the caller's items are.
 */
function shuffleIndices({ entropy, requestId, n }) {
  const rng = makeRng(entropy, 'shuffle', requestId);
  const a = [];
  for (let i = 0; i < n; i++) a.push(i);
  for (let i = a.length - 1; i > 0; i--) {
    const j = rng.randbelow(i + 1);
    const t = a[i];
    a[i] = a[j];
    a[j] = t;
  }
  return { kind: 'shuffle', params: { items: null, n }, output: a, rng };
}

/**
 * k distinct indices out of n, partial Fisher-Yates (no replacement).
 * Identical to verify.js `compute("lottery", …, {entries: [0..n-1], k})`.
 */
function sampleIndices({ entropy, requestId, n, k }) {
  const rng = makeRng(entropy, 'lottery', requestId);
  const a = [];
  for (let i = 0; i < n; i++) a.push(i);
  const out = [];
  for (let i = 0; i < k; i++) {
    const j = i + rng.randbelow(a.length - i);
    const t = a[i];
    a[i] = a[j];
    a[j] = t;
    out.push(a[i]);
  }
  return { kind: 'lottery', params: { entries: null, k, n }, output: out, rng };
}

/** k uniform indices out of n, WITH replacement (repeated randbelow(n)). */
function uniformIndices({ entropy, requestId, n, k }) {
  const rng = makeRng(entropy, 'choice', requestId);
  const out = [];
  for (let i = 0; i < k; i++) out.push(rng.randbelow(n));
  return { kind: 'choice', params: { items: null, n, k }, output: out, rng };
}

/** Cumulative-weight search over one uniform draw (verify.js weightedIndex). */
function weightedIndexFrom(rng, weights) {
  let total = 0;
  for (const w of weights) total += w;
  const r = rng.randbelow(total);
  let acc = 0;
  for (let i = 0; i < weights.length; i++) {
    acc += weights[i];
    if (r < acc) return i;
  }
  return weights.length - 1; // unreachable for total > 0; mirrors verify.js
}

/**
 * k weighted indices. `replace=true` repeats the cumulative search over the
 * full weight vector; `replace=false` repeats it over the REMAINING items
 * (original order preserved, the chosen entry spliced out, the total
 * recomputed) — the standard successive-draws formulation.
 * With k = 1 both are byte-identical to verify.js
 * `compute("weighted", …, {items: [0..n-1], weights})`.
 */
function weightedIndices({ entropy, requestId, weights, k, replace }) {
  const rng = makeRng(entropy, 'weighted', requestId);
  const out = [];
  if (replace) {
    for (let i = 0; i < k; i++) out.push(weightedIndexFrom(rng, weights));
    return { kind: 'weighted', params: { items: null, weights }, output: out, rng };
  }
  const idx = weights.map((_, i) => i);
  const w = weights.slice();
  for (let i = 0; i < k; i++) {
    const j = weightedIndexFrom(rng, w);
    out.push(idx[j]);
    idx.splice(j, 1);
    w.splice(j, 1);
  }
  return { kind: 'weighted', params: { items: null, weights }, output: out, rng };
}

/** Human-readable rule text embedded in every response's derivation block. */
const RULES = {
  domain: DOMAIN,
  seed: 'seed = sha3_256("animica/qrng/public/v1" || "|k:" || kind || "|r:" || request_id || "|b:" || bytes(randomness))',
  stream: 'stream = sha3_256(seed || be8(counter)) for counter = 0,1,2,… concatenated; consumed left to right, never reused',
  uniform_int:
    'randbelow(n): k = floor((bitlen(n)+7)/8)+1 bytes per attempt, read big-endian as x; limit = 2^(8k); accept when x < limit - (limit mod n); value = x mod n. Rejected attempts still consume their k bytes. n <= 1 consumes nothing and returns 0. This is rejection sampling — plain "x mod n" would bias small values.',
  shuffle:
    'Fisher-Yates over indices [0..N-1]: for i = N-1 down to 1 { j = randbelow(i+1); swap(a[i], a[j]) }. Applying the permutation to your items is equivalent to shuffling them directly.',
  sample_without_replacement:
    'partial Fisher-Yates: for i = 0..k-1 { j = i + randbelow(N-i); swap(a[i], a[j]); emit a[i] }',
  sample_with_replacement: 'for i = 0..k-1 { emit randbelow(N) }',
  weighted:
    'cumulative-weight search over ONE uniform draw: total = sum(weights); r = randbelow(total); the answer is the smallest i with r < weights[0]+…+weights[i]. Without replacement the chosen entry is removed (order of the rest preserved) and the search repeats over the remaining weights.',
};

const VERIFIER = {
  file: 'randomness/beacon_api/static/verify.js (Animica repo, github.com/animicaorg) — zero-dependency, runs in Node and the browser',
  api: 'AnimicaBeacon.drngSeed(entropy, kind, request_id) + AnimicaBeacon.QDRNG reproduce the stream; AnimicaBeacon.verifyResult(derivation.recompute) === true whenever `recompute` is present',
  note: 'verify.js calls the seed material `beacon` because it was written for beacon rounds. This product is NOT a beacon round: the seed material is the raw `randomness` bytes above, which the node signed (see `verification`).',
};

module.exports = {
  DOMAIN,
  RULES,
  VERIFIER,
  QDRNG,
  be8,
  bitLength,
  drngSeed,
  sha3_256,
  sha3Hex,
  hexToBytes,
  bytesToHex,
  makeRng,
  uniformInts,
  shuffleIndices,
  sampleIndices,
  uniformIndices,
  weightedIndexFrom,
  weightedIndices,
};
