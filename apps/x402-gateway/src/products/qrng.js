'use strict';
/**
 * P1 — verifiable randomness, $0.01/draw (X402_QRNG_PRICE_USDC).
 *
 * Wraps the REAL Animica randomness surface (`rand.quantumRandomBytes` on
 * the local node RPC) — never reimplemented, never embellished. Deployment
 * reality this product tells the truth about (QRNG recon, 2026-08-15):
 *
 *   - The node's entropy source today is `software-fallback` (os.urandom)
 *     and the signer is a software ed25519 key => `attested: false`, `mode:
 *     "pseudo"` everywhere. Those fields pass through VERBATIM; this
 *     product is "verifiable randomness (quantum-attested when hardware
 *     providers are connected)" — never unconditional hardware-quantum.
 *   - There is no Merkle proof / chain anchor / per-draw signature to
 *     expose. What EXISTS is the signed digest attestation:
 *       digest_hex == sha3_256(random bytes)
 *       ed25519_verify(public_key_hex, message = raw digest bytes, sig)
 *     and that is exactly what `verification` describes.
 *   - The RPC never fails closed on a sick source: it answers 200 with
 *     health.passed:false. THIS product enforces health.passed (and
 *     surfaces min_entropy_per_byte) — an unhealthy source is a 503 with
 *     no payment requested, per the spec's readiness-before-payment rule.
 *
 * Order of operations (fail-closed, per the recon): the readiness probe IS
 * a real randomness fetch; on a paid request the bytes are obtained and
 * health-checked BEFORE settlement, and the already-obtained result is
 * delivered after it. Never settle first and fetch after.
 *
 * The client-recomputable beacon+draw lane (QUW rounds / `animica beacon
 * serve`) is a documented later upgrade once the beacon sidecar is deployed
 * — see docs/x402.md.
 *
 * `createRandomnessSource` below is the ONE node call path for the whole
 * randomness family (src/products/random.js): int, shuffle, pick, bulk and
 * commit all buy a single verified draw through it and then derive
 * deterministically (src/products/derive.js). No product ever calls the node
 * once per output item, and every product's readiness is the SAME probe.
 */

const { ProductError, ProductUnavailable } = require('./errors');

const VERIFY_JS_POINTER =
  'randomness/beacon_api/static/verify.js in the animica repo (github.com/animicaorg) — dependency-free Node module exporting sha3_256; pair with any ed25519 verifier';

function buildVerification(result) {
  return {
    method: 'signed-digest-attestation',
    rules: [
      // Verified against rpc/methods/quantum.py::quantum_random_bytes:
      // digest = sha3_256(out).digest(); signature = signer.sign(digest) —
      // the message is the RAW 32 digest bytes.
      'attestation.digest_hex == sha3_256(bytes(randomness))',
      'ed25519_verify(pubkey=attestation.public_key_hex, message=raw_bytes(attestation.digest_hex), signature=attestation.signature_hex)',
    ],
    verifier: VERIFY_JS_POINTER,
    trust_model:
      'signed by the serving node, not client-recomputable: you trust the node\'s entropy source, then verify it signed exactly these bytes. Check health.passed and attestation.attested before relying on it.',
    attested: Boolean(result.attestation && result.attestation.attested),
  };
}

/**
 * The shared randomness source: ONE verified node draw per request plus the
 * fail-closed health gate, and ONE memoized readiness probe every product in
 * the family reuses (a catalog scrape must not fan out into N node calls).
 *
 * `probeTtlMs` comes from the registry so tests can disable memoization.
 */
function createRandomnessSource({ cfg, node, probeTtlMs = 2000, now = Date.now }) {
  const timeoutMs = cfg.qrngTimeoutMs;

  async function fetchRandom(n) {
    let result;
    try {
      result = await node.call('rand.quantumRandomBytes', { n, attested: true }, { timeoutMs });
    } catch (e) {
      throw new ProductUnavailable('qrng_rpc_unreachable', `randomness RPC failed: ${e.message}`);
    }
    if (!result || typeof result.bytes_hex !== 'string') {
      throw new ProductUnavailable('qrng_bad_response', 'randomness RPC returned no bytes');
    }
    if (!result.health || result.health.passed !== true) {
      // The RPC answers 200 even for a sick source — the gateway is the
      // fail-closed layer. No payment is requested for unhealthy entropy.
      throw new ProductUnavailable('qrng_entropy_health_failed',
        `entropy health gate failed (min_entropy_per_byte=${result.health ? result.health.min_entropy_per_byte : 'unknown'})`);
    }
    return result;
  }

  // Readiness = a real (tiny) randomness fetch, health gate included. Never
  // throws: an unavailable source is a value, so the paywall answers 503
  // BEFORE any 402 (never charge for a service known unavailable).
  let at = 0;
  let cached;
  let pending = null;
  function probe() {
    if (pending) return pending;
    if (cached !== undefined && now() - at < probeTtlMs) return Promise.resolve(cached);
    pending = fetchRandom(8)
      .then(() => ({ available: true }))
      .catch((e) => ({ available: false, reason: e.reason || 'qrng_unavailable', detail: e.message }))
      .then((v) => { cached = v; at = now(); pending = null; return v; });
    return pending;
  }

  /** Copy through ONLY the honesty fields the RPC actually returned. */
  function attachHonesty(bodyObj, result) {
    if (result.source !== undefined) bodyObj.source = result.source;
    if (result.health !== undefined) bodyObj.health = result.health;
    if (result.attestation !== undefined) bodyObj.attestation = result.attestation;
    bodyObj.verification = buildVerification(result);
    return bodyObj;
  }

  return { fetchRandom, probe, attachHonesty, buildVerification };
}

function createQrngProduct({ cfg, node, source }) {
  const src = source || createRandomnessSource({ cfg, node });
  const { fetchRandom } = src;

  return {
    id: 'qrng',
    title: 'Verifiable randomness',
    description:
      'Random bytes from the Animica node randomness service with a signed digest attestation and entropy-health report. Quantum-attested when hardware providers are connected; source/attested fields state the current truth.',
    path: '/x402/qrng/draw',
    routes: [
      { method: 'GET', path: '/x402/qrng/draw' },
      { method: 'POST', path: '/x402/qrng' },
    ],
    priceUsd: cfg.qrngPriceUsd,
    enabled: cfg.qrngEnabled,
    mode: 'execute-then-settle',
    mimeType: 'application/json',
    outputSchema: {
      input: {
        type: 'http',
        method: 'GET',
        queryParams: {
          bytes: { type: 'integer', description: `number of random bytes, 1..${cfg.qrngMaxBytes} (default 32); alias: n` },
        },
      },
      output: {
        type: 'json',
        description:
          'randomness (hex), bytes, source, health {passed, min_entropy_per_byte}, attestation {alg, public_key_hex, digest_hex, signature_hex, attested}, verification rules, payment metadata',
      },
    },

    /** Readiness = a real (tiny) randomness fetch, health gate included. */
    availability() {
      return src.probe();
    },

    /** Param validation happens before any payment is requested. */
    validate(ctx) {
      const raw = ctx.query.get('bytes') ?? ctx.query.get('n') ?? '32';
      const n = Number(raw);
      if (!Number.isInteger(n) || n < 1 || n > cfg.qrngMaxBytes) {
        throw new ProductError(`bytes must be an integer in [1, ${cfg.qrngMaxBytes}]`, {
          body: { error: 'invalid_params', detail: `bytes must be an integer in [1, ${cfg.qrngMaxBytes}]` },
        });
      }
      return { n };
    },

    /**
     * Runs after verify, BEFORE settle (execute-then-settle): a failure
     * here charges nobody. Only fields the RPC actually returned appear.
     */
    async handler(ctx) {
      const result = await fetchRandom(ctx.params.n);
      const bodyObj = src.attachHonesty({
        product: 'qrng',
        randomness: result.bytes_hex,
        encoding: 'hex',
        bytes: result.n,
      }, result);
      return { status: 200, bodyObj };
    },
  };
}

module.exports = { createQrngProduct, createRandomnessSource, buildVerification, VERIFY_JS_POINTER };
