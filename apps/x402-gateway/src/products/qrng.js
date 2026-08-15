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

  // The honesty triple observed on the LAST successful draw. The readiness
  // probe already fetches a real draw every few seconds, so the current truth
  // about the entropy source is known for free — publishing it in the catalog
  // and in the 402 offer is what lets a buyer (or a directory) learn that the
  // source is a software CSPRNG BEFORE paying, instead of after.
  let lastHonesty = null;

  function recordHonesty(result) {
    const s = result.source || {};
    const a = result.attestation || {};
    const h = result.health || {};
    lastHonesty = {
      source: s.name === undefined ? null : s.name,
      vendor: s.vendor === undefined ? null : s.vendor,
      model: s.model === undefined ? null : s.model,
      is_hardware: s.is_hardware === undefined ? null : Boolean(s.is_hardware),
      is_quantum: s.is_quantum === undefined ? null : Boolean(s.is_quantum),
      attested: a.attested === undefined ? null : Boolean(a.attested),
      signer_backend: a.backend === undefined ? null : a.backend,
      health_passed: h.passed === undefined ? null : Boolean(h.passed),
      min_entropy_per_byte: h.min_entropy_per_byte === undefined ? null : h.min_entropy_per_byte,
      observed_at: new Date(now()).toISOString(),
      note:
        'observed on the gateway\'s own readiness draw, published unpaid so the trust model is knowable before purchase; '
        + 'the same fields ride verbatim on every paid response',
    };
  }

  /**
   * The current, pre-purchase truth about the entropy source. Never a claim:
   * either the fields the node last reported, or an explicit "not observed
   * yet". Callers publish this in the free catalog and in the 402 offer.
   */
  function honesty() {
    if (lastHonesty) return lastHonesty;
    return {
      source: null,
      is_hardware: null,
      is_quantum: null,
      attested: null,
      health_passed: null,
      observed_at: null,
      note: 'no successful draw observed yet on this process — the paid response always carries the node\'s own source/health/attestation fields verbatim',
    };
  }

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
    recordHonesty(result);
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

  return { fetchRandom, probe, attachHonesty, buildVerification, honesty };
}

function createQrngProduct({ cfg, node, source }) {
  const src = source || createRandomnessSource({ cfg, node });
  const { fetchRandom } = src;

  const BYTES_FIELDS = new Set(['bytes', 'n']);

  return {
    id: 'qrng',
    title: 'Signed random bytes',
    description:
      `Random bytes from the Animica node's randomness service with a signed digest attestation and an entropy-health report, 1..${cfg.qrngMaxBytes} bytes per draw. Current trust model, stated up front and in every response: the entropy source is a software CSPRNG (os.urandom) and the signer is a software key, so source.is_quantum=false and attestation.attested=false today; those fields flip truthfully if a hardware/quantum provider is ever connected. The free catalog entry carries the same live entropy block, so you can check the source before paying.`,
    path: '/x402/qrng/draw',
    routes: [
      { method: 'GET', path: '/x402/qrng/draw' },
      { method: 'POST', path: '/x402/qrng' },
    ],
    priceUsd: cfg.qrngPriceUsd,
    enabled: cfg.qrngEnabled,
    mode: 'execute-then-settle',
    mimeType: 'application/json',
    // Explicit so the shipped nginx caps can be checked against it
    // (test/nginx-conf.test.js): the app must be the layer that rejects an
    // oversize body with structured JSON, never nginx with an HTML 413.
    maxBodyBytes: 8 * 1024,
    /** Live pre-purchase honesty for the catalog and the 402 offer. */
    entropyDisclosure: () => src.honesty(),
    outputSchema: {
      input: {
        type: 'http',
        method: 'GET',
        queryParams: {
          bytes: { type: 'integer', description: `number of random bytes, 1..${cfg.qrngMaxBytes} (default 32); alias: n` },
        },
        // POST /x402/qrng takes the SAME parameters as a JSON body. Declared
        // here so an indexer advertises the right shape for both routes — a
        // POST caller sending {"bytes":256} must not be silently answered
        // with 32 bytes.
        alternateMethods: [{
          method: 'POST',
          path: '/x402/qrng',
          bodyType: 'json',
          bodyFields: {
            bytes: { type: 'integer', required: false, description: `number of random bytes, 1..${cfg.qrngMaxBytes} (default 32); alias: n` },
          },
          note: 'query parameters also work on POST; supplying both with different values is a 400 rather than a silent winner',
        }],
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

    /**
     * Param validation happens before any payment is requested — and it
     * reads the POST JSON body as well as the query string. Ignoring a body
     * would charge a POST caller $0.01 for a request whose only parameter
     * was discarded.
     */
    validate(ctx) {
      const badParams = (detail, error = 'invalid_params') => new ProductError(detail, { body: { error, detail } });
      let bodyValue;
      if (ctx.method === 'POST' || ctx.method === 'PUT') {
        if (ctx.rawBody && ctx.rawBody.length) {
          if (ctx.json === null || typeof ctx.json !== 'object' || Array.isArray(ctx.json)) {
            throw badParams('a POST body must be a JSON object with content-type: application/json (or omit it and use ?bytes=)', 'invalid_body');
          }
          const unknown = Object.keys(ctx.json).filter((k) => !BYTES_FIELDS.has(k));
          if (unknown.length) {
            throw badParams(`unknown body field(s): ${unknown.join(', ')} — this endpoint takes only bytes (alias n)`);
          }
          bodyValue = ctx.json.bytes ?? ctx.json.n;
          if (bodyValue !== undefined && bodyValue !== null
              && !(typeof bodyValue === 'number' || (typeof bodyValue === 'string' && /^-?\d+$/.test(bodyValue)))) {
            throw badParams('bytes must be an integer');
          }
        }
      }
      const queryRaw = ctx.query.get('bytes') ?? ctx.query.get('n');
      if (bodyValue !== undefined && bodyValue !== null && queryRaw !== null && String(bodyValue) !== String(queryRaw)) {
        throw badParams(`bytes given twice with different values (query ${queryRaw}, body ${bodyValue}) — send it once`, 'conflicting_params');
      }
      const raw = queryRaw ?? (bodyValue === undefined || bodyValue === null ? '32' : bodyValue);
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
