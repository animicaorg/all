'use strict';
/**
 * x402 gating middleware: wraps a route handler so the resource is only
 * served against a verified payment, then settled after serving (the spec's
 * default `authorization` flow: verify -> resource -> settle).
 *
 * Two lanes are offered in every 402:
 *
 *   A. USDC on Base ("exact" EVM scheme) via the configured facilitator —
 *      our own by default (X402_FACILITATOR_MODE=self, loopback :8743), or
 *      any external x402 v2 §7-compatible facilitator the operator names in
 *      X402_FACILITATOR_URL. This is the lane that makes endpoints indexable
 *      by the x402 discovery ecosystem (e.g. x402scan).
 *   B. wANM — the SPL token minted by the solana.animica.org bridge — via
 *      the LOCAL self-facilitator (src/facilitator.js). This is the ANM
 *      utility lane; no third party sits between payer and treasury.
 *
 * The handler contract is a descriptor, not (req,res) streaming:
 *      serve(req) -> { status, headers, body }
 * because the authorization flow needs the resource fully produced BEFORE
 * settlement, and the settlement result travels in a response header —
 * headers are gone once a byte of body has been streamed.
 *
 * Failure ordering is deliberate: if /settle fails after the resource was
 * produced, the resource is NOT delivered (402 with the settle error). We eat
 * the compute; the payer keeps their money. The alternative — deliver and
 * hope — turns every settle outage into free service.
 */

const cfgMod = require('./config');
const protocol = require('./protocol');
const { links, networkFacts, PROVIDER } = require('./discovery/links');

/** POST to a facilitator, x402 v2 §7 contract. */
function facilitatorClient(baseUrl, { fetchImpl = fetch, timeoutMs = 20_000, settleTimeoutMs } = {}) {
  // Settling waits for an on-chain receipt + confirmations — it legitimately
  // runs far past a verify's budget. A settle clipped by a short client
  // timeout looks failed while the tx lands anyway (the money moves and the
  // resource is withheld), so it gets its own generous deadline.
  const settleBudget = settleTimeoutMs || Math.max(timeoutMs, 75_000);
  async function post(path, body, budget = timeoutMs) {
    const res = await fetchImpl(baseUrl.replace(/\/$/, '') + path, {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify(body),
      signal: AbortSignal.timeout(budget),
    });
    if (!res.ok) throw new Error(`facilitator ${path} http ${res.status}`);
    return res.json();
  }
  return {
    verify: (paymentPayload, paymentRequirements) =>
      post('/verify', { x402Version: 2, paymentPayload, paymentRequirements }),
    settle: (paymentPayload, paymentRequirements) =>
      post('/settle', { x402Version: 2, paymentPayload, paymentRequirements }, settleBudget),
    supported: async () => {
      const res = await (fetchImpl)(baseUrl.replace(/\/$/, '') + '/supported', {
        signal: AbortSignal.timeout(timeoutMs),
      });
      if (!res.ok) throw new Error(`facilitator /supported http ${res.status}`);
      return res.json();
    },
  };
}

/**
 * Build the accepts array for a route. A lane is only offered when its
 * configuration is complete — a half-configured lane in an accepts array is
 * an invitation to pay an address nobody controls.
 */
function buildAccepts(route, cfg) {
  const accepts = [];
  if (cfg.basePayTo && cfg.usdcAsset) {
    accepts.push({
      scheme: 'exact',
      network: cfg.networkEvm,
      amount: cfgMod.usdToUsdcAtomic(route.priceUsd),
      asset: cfg.usdcAsset,
      payTo: cfg.basePayTo,
      maxTimeoutSeconds: cfg.maxTimeoutSeconds,
    });
  }
  // The wANM/SVM lane is RETIRED (the bridge was abandoned 2026-08-15) and its
  // self-facilitator keeps replay marks in memory only — a restart would forget
  // them, leaving a cross-restart double-settle window. Offering it would be
  // offering an unsafe rail, so it is refused outright rather than merely
  // left unconfigured. Re-enabling requires porting it to the persistent
  // store used by the EVM facilitator.
  if (cfg.wanmMint && cfg.wanmTreasury && cfg.wanmFeePayerPubkey && cfg.wanmUsdPrice
      && cfg.allowRetiredWanmLane) {
    accepts.push({
      scheme: 'exact',
      network: cfg.networkSvm,
      amount: cfgMod.usdToTokenAtomic(route.priceUsd, cfg.wanmUsdPrice, cfg.wanmDecimals),
      asset: cfg.wanmMint,
      payTo: cfg.wanmTreasury,
      maxTimeoutSeconds: cfg.maxTimeoutSeconds,
      extra: { feePayer: cfg.wanmFeePayerPubkey },
    });
  }
  return accepts;
}

/**
 * OPTIONAL descriptive metadata for a 402 (discovery spec §2): who is
 * selling, what, for how much, and where the human documentation is. It
 * rides in `extensions.animica`, i.e. in the part of the wire format that
 * exists for exactly this — no client is required to read it, and nothing
 * here is a protocol field a payer must understand to pay.
 *
 * Every payment-critical value is copied from the accepts entry BUILT IN THE
 * SAME CALL, so the descriptive copy of the price can never disagree with the
 * terms actually being offered.
 */
function describeRoute(route, cfg, accepts) {
  if (!route.productId) return null; // legacy/demo routes describe nothing
  const l = links(cfg);
  const facts = networkFacts(cfg);
  const terms = accepts[0] || null;
  const out = {
    product: route.productId,
    name: route.productName || route.productId,
    description: route.description || '',
    price: route.priceUsd,
    currency: facts.asset || undefined,
    content_type: route.mimeType || 'application/json',
    documentation: l.docFor(route.productId),
    catalog: l.wellKnown,
    openapi: l.openapi,
    provider: PROVIDER,
    x402_version: 2,
  };
  if (terms) {
    out.terms = {
      scheme: terms.scheme,
      network: terms.network,
      chain_id: facts.chain_id,
      amount_atomic: terms.amount,
      asset: terms.asset,
      pay_to: terms.payTo,
    };
  }
  return out;
}

function buildPaymentRequiredForRoute(route, cfg, error) {
  const accepts = buildAccepts(route, cfg);
  const extensions = {};
  // Discovery extension (v2): indexers read info.{input,output} to learn what
  // the endpoint takes. `bazaarExtra` carries per-product facts a buyer needs
  // BEFORE paying — today the randomness family's live `entropy` block
  // (source, is_quantum, attested), so the trust model is in the offer.
  //
  // Emitted under TWO keys with identical content. `discovery` is the
  // vendor-neutral name and is the one to build against. `bazaar` is a
  // compatibility alias: that is the key x402scan and other existing indexers
  // parse today, and dropping it would make our input/output schemas invisible
  // to them. It names a JSON shape, not a service — this gateway calls no
  // third-party discovery API and self-hosts its facilitator. Retire the alias
  // once the indexers that matter read `discovery`.
  if (route.outputSchema || route.bazaarExtra) {
    const info = Object.assign(
      route.outputSchema ? { input: route.outputSchema.input, output: route.outputSchema.output } : {},
      route.bazaarExtra || {}
    );
    extensions.discovery = { info };
    extensions.bazaar = { info };
  }
  const described = describeRoute(route, cfg, accepts);
  if (described) extensions.animica = described;
  return protocol.buildPaymentRequired({
    resource: {
      url: cfg.resourceBaseUrl.replace(/\/$/, '') + route.path,
      description: route.description || '',
      mimeType: route.mimeType || 'application/json',
      serviceName: cfg.serviceName,
    },
    accepts,
    extensions: Object.keys(extensions).length ? extensions : undefined,
    error,
  });
}

function createX402Gate(options = {}) {
  const cfg = cfgMod.load(options);
  const fetchImpl = options.fetchImpl || fetch;
  const logger = options.logger || console;

  function facilitatorFor(network) {
    if (network.startsWith('eip155:')) return facilitatorClient(cfg.evmFacilitatorUrl, { fetchImpl });
    if (network.startsWith('solana:')) return facilitatorClient(cfg.svmFacilitatorUrl, { fetchImpl });
    throw new Error(`no facilitator for network ${network}`);
  }

  function send(res, status, headers, body) {
    res.writeHead(status, headers);
    res.end(body);
  }

  function send402(res, route, error, wireVersion) {
    const paymentRequired = buildPaymentRequiredForRoute(route, cfg, error);
    // v2 reads the PAYMENT-REQUIRED header; v1 reads the JSON body. Send the
    // v2 object through the header and the v1 rendering through the body so
    // each client generation sees its own dialect on its own channel.
    const body = JSON.stringify(protocol.toV1Body(paymentRequired, route.outputSchema || null), null, 2);
    send(res, 402, {
      'content-type': 'application/json',
      'payment-required': protocol.encodeHeader(paymentRequired),
    }, body);
  }

  /**
   * Gate `serve` behind x402 for `route` ({path, priceUsd, description?,
   * mimeType?}). Returns an async (req, res) handler.
   */
  function gate(route, serve) {
    if (!route || !route.path || !route.priceUsd) throw new Error('route needs path and priceUsd');
    return async function gated(req, res) {
      // Kill switch first: with the flag off this scaffold must not gate —
      // and must not serve for free either. 503 is the honest answer.
      if (!cfg.enabled) {
        return send(res, 503, { 'content-type': 'application/json' },
          JSON.stringify({ error: 'x402_disabled', detail: 'set ANM_X402_ENABLED=1' }));
      }

      const accepts = buildAccepts(route, cfg);
      if (accepts.length === 0) {
        logger.error(`x402 route ${route.path} has no configured lanes`);
        return send(res, 503, { 'content-type': 'application/json' },
          JSON.stringify({ error: 'x402_unconfigured' }));
      }

      let parsed;
      try {
        parsed = protocol.parsePaymentHeaders(req.headers, accepts);
      } catch (e) {
        if (e instanceof protocol.PaymentParseError) return send402(res, route, e.message);
        throw e;
      }
      if (!parsed) return send402(res, route); // no payment offered

      const { wireVersion, paymentPayload } = parsed;

      // The client must have accepted terms we actually offered, verbatim.
      // Comparing against OUR freshly built accepts (not anything echoed by
      // the client) is what makes amount/payTo tampering inert.
      const matched = accepts.find((a) => protocol.requirementsEqual(a, paymentPayload.accepted));
      if (!matched) return send402(res, route, 'accepted requirements do not match this server\'s offer');

      let fc;
      try {
        fc = facilitatorFor(matched.network);
      } catch (e) {
        return send402(res, route, e.message);
      }

      let verdict;
      try {
        verdict = await fc.verify(paymentPayload, matched);
      } catch (e) {
        logger.error('facilitator verify unreachable', e.message);
        return send(res, 502, { 'content-type': 'application/json' },
          JSON.stringify({ error: 'facilitator_unreachable' }));
      }
      if (!verdict.isValid) {
        return send402(res, route, verdict.invalidReason || 'payment invalid');
      }

      // Produce the resource BEFORE settling (authorization flow) but hold
      // delivery until settlement succeeds.
      let out;
      try {
        out = await serve(req);
      } catch (e) {
        logger.error(`gated handler for ${route.path} threw`, e);
        // Nothing was taken: we have not settled. Fail without charging.
        return send(res, 500, { 'content-type': 'application/json' },
          JSON.stringify({ error: 'internal_error' }));
      }

      let settlement;
      try {
        settlement = await fc.settle(paymentPayload, matched);
      } catch (e) {
        logger.error('facilitator settle unreachable', e.message);
        settlement = { success: false, errorReason: 'unexpected_settle_error', transaction: '', network: matched.network };
      }
      const settlementResponse = protocol.buildSettlementResponse(settlement);
      const settlementHeaderValue = protocol.encodeHeader(settlementResponse);
      // The v1 wire names networks by slug, not CAIP-2 — the X-PAYMENT-
      // RESPONSE header must speak that dialect (transports-v1/http.md).
      const v1SettlementHeaderValue = protocol.encodeHeader(Object.assign({}, settlementResponse, {
        network: cfgMod.V1_NETWORK_SLUGS[settlementResponse.network] || settlementResponse.network,
      }));

      if (!settlement.success) {
        const headers = {
          'content-type': 'application/json',
          'payment-required': protocol.encodeHeader(
            buildPaymentRequiredForRoute(route, cfg, settlement.errorReason || 'settlement failed')),
        };
        headers[wireVersion === 1 ? protocol.HEADER_X_PAYMENT_RESPONSE : protocol.HEADER_PAYMENT_RESPONSE] =
          wireVersion === 1 ? v1SettlementHeaderValue : settlementHeaderValue;
        return send(res, 402, headers,
          JSON.stringify({ x402Version: wireVersion, error: settlement.errorReason || 'settlement failed' }));
      }

      const headers = Object.assign({ 'content-type': route.mimeType || 'application/json' }, out.headers);
      // Settlement proof on the version's own channel; v2 name also sent to
      // v1 clients costs nothing and helps mixed SDKs.
      headers[protocol.HEADER_PAYMENT_RESPONSE] = settlementHeaderValue;
      if (wireVersion === 1) headers[protocol.HEADER_X_PAYMENT_RESPONSE] = v1SettlementHeaderValue;
      return send(res, out.status || 200, headers, out.body);
    };
  }

  return { cfg, gate, buildAccepts: (route) => buildAccepts(route, cfg), buildPaymentRequiredForRoute: (route, error) => buildPaymentRequiredForRoute(route, cfg, error), facilitatorClient };
}

module.exports = { createX402Gate, facilitatorClient, buildAccepts, buildPaymentRequiredForRoute };
