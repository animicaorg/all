'use strict';
/**
 * P3 — priority inference, $0.10/request (X402_INFERENCE_PRICE_USDC).
 * POST /x402/v1/chat/completions — DISABLED BY DEFAULT
 * (PRIORITY_INFERENCE_ENABLED=0) and additionally gated on live serving
 * capacity (src/capacity.js): below
 * PRIORITY_INFERENCE_MIN_SERVING_WORKERS the catalog reports
 * available:false, the gateway never emits a 402, and the endpoint answers
 * a clear 503. NEVER take payment for a service known unavailable — and
 * capacity is re-checked immediately before settlement, so a worker drop
 * between the 402 and the paid retry costs the payer nothing.
 *
 * What the paid path HONESTLY adds over the free https://animica.dev/v1
 * (documented in docs/x402.md; never charge for the identical unreliable
 * free path):
 *   - capacity-gated admission: a paid request is only accepted while >= N
 *     workers are live-serving the configured tier — the free path accepts
 *     requests into a dead pool and times out;
 *   - direct upstream routing (X402_INFERENCE_UPSTREAM_URL, default the
 *     serving bridge on loopback): skips the public nginx proxy and its
 *     per-IP rate-limit queue entirely — paid requests are not subject to
 *     the free tier's 30 req/min/IP limit;
 *   - a long per-request timeout (X402_INFERENCE_TIMEOUT_MS, default
 *     180 s) sized for real community-GPU latency, vs the free front-end
 *     budgets that abort long generations;
 *   - the payment-then-service failure policy: if the upstream still fails
 *     after settlement, the payer gets a signed error receipt and an
 *     incident row is opened for refund reconciliation (src/paywall.js).
 * What it does NOT add (yet): reserved capacity or model exclusivity —
 * the same workers serve both lanes. Streaming responses are not yet
 * supported on the paid path (settlement proof travels in response
 * headers): stream:true is rejected before payment.
 */

const { ProductError } = require('./errors');

/**
 * THE BRIDGE APOLOGISES WITH HTTP 200.
 *
 * When every AICF worker that claims a job returns a stub (a provider that
 * could not load a model), the chat bridge gives up after its budget and
 * answers 200 with a plain-language fallback in `choices[0].message.content`.
 * Passing that through as a delivered result means a payer is CHARGED FOR AN
 * APOLOGY — the precise thing this codebase's rule forbids ("never take money
 * for a service known unavailable"). A 200 is not proof of service.
 *
 * These markers are the bridge's own fallback text and the worker stub
 * prefixes it cycles on. Matching is deliberately narrow: it must never
 * misclassify a genuine answer that merely discusses model loading, so it
 * anchors on the fallback's distinctive opening rather than loose keywords.
 */
const DEGRADED_MARKERS = [
  'The Animica AI network couldn\'t complete your request',
  'wasn\'t able to load a language model',
];

function degradedAnswer(text) {
  if (!text) return null;
  let body;
  try {
    body = JSON.parse(text);
  } catch {
    return null;              // not JSON: let the caller decide
  }
  const content = body && body.choices && body.choices[0]
    && body.choices[0].message && body.choices[0].message.content;
  if (typeof content !== 'string') return null;
  const head = content.slice(0, 400);
  for (const m of DEGRADED_MARKERS) {
    if (head.includes(m)) return content.slice(0, 200);
  }
  return null;
}

function createPriorityInferenceProduct({ cfg, capacity, fetchImpl = fetch, now = Date.now }) {
  /**
   * Circuit breaker over the DEGRADED-ANSWER signal.
   *
   * The capacity gate can only see what the chain reports — registered, fresh
   * last_seen, advertised tier. A worker satisfying all three can still fail
   * to load a model and return a stub, and that is exactly the state observed
   * on 2026-08-18: one worker advertising `standard`, live, and unable to
   * serve. Advertising through that is how a catalog says available:true for
   * something that cannot be delivered.
   *
   * So actual delivery failures feed back into availability: after
   * `X402_INFERENCE_BREAKER_TRIPS` degraded answers the product reports
   * unavailable for a cooldown, which stops the catalog advertising it AND
   * stops the paywall taking money for it. One genuine answer clears it.
   */
  const breaker = {
    fails: 0,
    openUntil: 0,
    trip() {
      this.fails += 1;
      if (this.fails >= Number(cfg.inferenceBreakerTrips)) {
        this.openUntil = now() + Number(cfg.inferenceBreakerCooldownMs);
      }
    },
    reset() { this.fails = 0; this.openUntil = 0; },
    open() { return now() < this.openUntil; },
    retryInSeconds() { return Math.max(0, Math.ceil((this.openUntil - now()) / 1000)); },
  };

  function gateBody() {
    const snap = capacity.snapshot();
    return {
      error: 'priority_inference_unavailable',
      serving_workers: snap.serving_workers,
      required: snap.required,
      enabled: snap.enabled,
      detail: snap.enabled
        ? `serving capacity below floor (${snap.serving_workers}/${snap.required} workers for tier ${snap.tier})`
        : 'priority inference is disabled by the operator (PRIORITY_INFERENCE_ENABLED=0)',
    };
  }

  return {
    id: 'priority_inference',
    title: 'Priority inference',
    description:
      'OpenAI-compatible chat completions with capacity-gated admission, no per-IP rate limit, direct upstream routing and a 180 s timeout. Only sold while enough workers are live-serving; disabled by default.',
    path: '/x402/v1/chat/completions',
    routes: [{ method: 'POST', path: '/x402/v1/chat/completions' }],
    priceUsd: cfg.inferencePriceUsd,
    enabled: true, // route exists; the capacity gate (not a 404) explains why it refuses
    listedEvenWhenUnavailable: true,
    mode: 'settle-then-execute',
    mimeType: 'application/json',
    maxBodyBytes: cfg.inferenceMaxBodyBytes,
    injectPayment: false, // keep the OpenAI response shape pure; proof rides in headers
    outputSchema: {
      input: {
        type: 'http',
        method: 'POST',
        bodyType: 'json',
        bodyFields: {
          model: { type: 'string', description: 'model id (see the free /v1/models)' },
          messages: { type: 'array', description: 'OpenAI-style chat messages' },
          max_tokens: { type: 'integer', description: 'completion budget' },
          stream: { type: 'boolean', description: 'must be false/absent — streaming is not yet supported on the paid path' },
        },
      },
      output: { type: 'json', description: 'OpenAI-compatible chat.completion object from the Animica serving bridge' },
    },

    /** Catalog + gate availability: operator flag AND live capacity. */
    async availability() {
      // Delivery reality beats chain metadata: if recent paid calls came back
      // as the network's degraded fallback, the product is NOT available no
      // matter what the workers advertise.
      if (breaker.open()) {
        return {
          available: false,
          reason: 'inference_network_degraded',
          detail:
            `every provider that recently claimed a job failed to load a model, so this product is withheld for ${breaker.retryInSeconds()}s rather than charging for an apology. It restores automatically as soon as one real answer is served.`,
        };
      }
      if (capacity.available()) {
        const snap = capacity.snapshot();
        return { available: true, serving_workers: snap.serving_workers, required: snap.required };
      }
      const body = gateBody();
      // Two distinct machine reasons in the CATALOG, because an agent
      // deciding whether to come back later needs to tell "the operator has
      // this switched off" from "the network is short of serving workers
      // right now". The 503 body keeps its single stable error code
      // (priority_inference_unavailable) for callers that already match on it.
      return {
        available: false,
        reason: body.enabled ? 'insufficient_serving_capacity' : 'priority_inference_disabled',
        detail: body.detail,
        body,
      };
    },

    validate(ctx) {
      if (!ctx.json || typeof ctx.json !== 'object' || Array.isArray(ctx.json)) {
        throw new ProductError('body must be a JSON object', { body: { error: 'invalid_request', detail: 'body must be a JSON object' } });
      }
      if (ctx.json.stream === true) {
        throw new ProductError('streaming not supported on the paid endpoint', {
          body: { error: 'stream_unsupported', detail: 'set stream:false — settlement proof travels in response headers, which streaming cannot carry yet' },
        });
      }
      if (!Array.isArray(ctx.json.messages) || ctx.json.messages.length === 0) {
        throw new ProductError('messages is required', { body: { error: 'invalid_request', detail: 'messages must be a non-empty array' } });
      }
      return {};
    },

    /**
     * MANDATORY capacity re-check immediately before settlement: capacity
     * may have dropped between the 402 and the paid retry. Refusing here
     * costs the payer nothing (their authorization was never consumed).
     */
    async preSettle() {
      // Force a SYNCHRONOUS probe here rather than trusting the background
      // one: the cached count may be up to maxProbeAgeMs old, and every
      // serving worker can vanish inside that window. This is the last moment
      // before the payer's USDC moves, so it has to reflect now, not a minute
      // ago. A probe failure is treated as unavailable (fail closed).
      try {
        await capacity.probeOnce();
      } catch (e) {
        const err = new ProductError('capacity probe failed', { status: 503, body: gateBody() });
        err.unavailable = true;
        throw err;
      }
      if (!capacity.available()) {
        const err = new ProductError('capacity dropped', { status: 503, body: gateBody() });
        err.unavailable = true;
        throw err;
      }
      return { capacity: capacity.snapshot() };
    },

    /** Proxy into the existing /v1 upstream. Network/5xx errors retry once. */
    async handler(ctx) {
      let res;
      try {
        res = await fetchImpl(cfg.inferenceUpstreamUrl, {
          method: 'POST',
          headers: Object.assign({
            'content-type': 'application/json',
            'x-animica-priority': 'x402-paid',
            'x-request-id': ctx.requestId || '',
          // An upstream that requires a key (e.g. the pool's /v1) gets one.
          // Optional: unset means the header is simply absent, so an
          // unauthenticated upstream is unaffected.
          }, cfg.inferenceUpstreamKey ? { authorization: `Bearer ${cfg.inferenceUpstreamKey}` } : {}),
          body: ctx.rawBody,
          signal: AbortSignal.timeout(cfg.inferenceTimeoutMs),
        });
      } catch (e) {
        const err = new Error(`inference upstream unreachable: ${e.message}`);
        err.retryable = true;
        throw err;
      }
      const text = await res.text();
      if (res.status >= 500) {
        const err = new Error(`inference upstream ${res.status}`);
        err.retryable = true;
        throw err;
      }
      // A 200 carrying the bridge's degraded-network fallback is NOT a
      // delivered service. Refuse it: in settle-then-execute this becomes a
      // signed receipt + incident (refundable) instead of a paid apology, and
      // it trips the breaker below so we stop advertising and stop charging.
      const degraded = degradedAnswer(text);
      if (degraded) {
        breaker.trip();
        const err = new Error(
          `the inference network could not serve this request (every provider that claimed it failed to load a model): ${degraded}`
        );
        err.retryable = false;
        throw err;
      }
      breaker.reset();
      // Upstream 4xx = the service answering a malformed request
      // deterministically; passed through with the settlement header.
      return {
        status: res.status,
        headers: { 'content-type': res.headers && res.headers.get ? (res.headers.get('content-type') || 'application/json') : 'application/json' },
        body: text,
      };
    },
  };
}


// Standard-tier inference sold through x402 — the cheaper, non-priority lane.
// Reuses the priority product's proven availability/settle/handler wiring; only
// the identity, endpoint, tier header and (dynamic) price differ. Forwards with
// x-animica-tier: standard so the upstream serves the standard tier.
function createTierStandardsProduct(deps) {
  const base = createPriorityInferenceProduct(deps);
  const cfg = deps.cfg;
  const fetchImpl = deps.fetchImpl || fetch;
  return Object.assign({}, base, {
    id: 'tier_standards',
    title: 'Tier Standards inference',
    description:
      'Standard-tier chat/completions (OpenAI-compatible), served by Animica\'s network. Cheaper than priority, standard latency — priced at 3x settlement gas.',
    path: '/x402/v1/standard/chat/completions',
    routes: [{ method: 'POST', path: '/x402/v1/standard/chat/completions' }],
    priceUsd: cfg.inferencePriceUsd, // the dynamic pricer overrides this to 3x gas
    async handler(ctx) {
      let res;
      try {
        res = await fetchImpl(cfg.inferenceUpstreamUrl, {
          method: 'POST',
          headers: {
            'content-type': 'application/json',
            'x-animica-tier': 'standard',
            'x-request-id': ctx.requestId || '',
          },
          body: ctx.rawBody,
          signal: AbortSignal.timeout(cfg.inferenceTimeoutMs),
        });
      } catch (e) {
        const err = new Error(`inference upstream unreachable: ${e.message}`);
        err.retryable = true;
        throw err;
      }
      const text = await res.text();
      if (res.status >= 500) {
        const err = new Error(`inference upstream ${res.status}`);
        err.retryable = true;
        throw err;
      }
      return {
        status: res.status,
        headers: { 'content-type': res.headers && res.headers.get ? (res.headers.get('content-type') || 'application/json') : 'application/json' },
        body: text,
      };
    },
  });
}

module.exports = { createPriorityInferenceProduct, createTierStandardsProduct };
