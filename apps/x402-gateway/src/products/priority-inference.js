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

function createPriorityInferenceProduct({ cfg, capacity, fetchImpl = fetch }) {
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
      if (capacity.available()) {
        const snap = capacity.snapshot();
        return { available: true, serving_workers: snap.serving_workers, required: snap.required };
      }
      const body = gateBody();
      return { available: false, reason: 'priority_inference_unavailable', detail: body.detail, body };
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
          headers: {
            'content-type': 'application/json',
            'x-animica-priority': 'x402-paid',
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

module.exports = { createPriorityInferenceProduct };
