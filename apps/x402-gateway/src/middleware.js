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
const { cdpAuthProvider, toStandardV2Payload } = require('./facilitator-cdp/auth');
const { buildCdpBazaarExtension } = require('./facilitator-cdp/bazaar');
const { createAnmPrice } = require('./anm-price');
const protocol = require('./protocol');
const { links, networkFacts, PROVIDER } = require('./discovery/links');

/** POST to a facilitator, x402 v2 §7 contract. */
/**
 * The Authorization provider for the EVM facilitator, or null.
 *
 * EXPORTED AND USED BY EVERY CALL SITE ON PURPOSE. There are two places that
 * build an EVM facilitator client — this module and `paywall.js` — and only the
 * paywall's is on the path a paid request actually takes. Wiring auth into one
 * of them produced a gateway that passed preflight, booted clean, logged
 * `facilitator_mode: remote`, and then 401'd on the first real payment because
 * the client doing the verifying had no credentials attached. Same shape as the
 * two-payTo-variables incident: a second construction site nobody updated.
 * Anything needing a facilitator auth header calls THIS.
 */
function evmAuthHeaderFor(cfg) {
  return cfg.facilitatorMode === 'remote' ? cdpAuthProvider(cfg) : null;
}

function facilitatorClient(baseUrl, { fetchImpl = fetch, timeoutMs = 20_000, settleTimeoutMs, authHeader = null, logger = null } = {}) {
  // Settling waits for an on-chain receipt + confirmations — it legitimately
  // runs far past a verify's budget. A settle clipped by a short client
  // timeout looks failed while the tx lands anyway (the money moves and the
  // resource is withheld), so it gets its own generous deadline.
  const settleBudget = settleTimeoutMs || Math.max(timeoutMs, 75_000);
  // A remote facilitator may require per-request authorisation (CDP mints a
  // JWT bound to the exact method+URI). The header is built PER CALL from the
  // URL being requested rather than once at construction, because a token
  // minted for /verify must not be usable against /settle.
  function headersFor(method, url) {
    const h = { 'content-type': 'application/json' };
    if (authHeader) h.authorization = authHeader(method, url);
    return h;
  }

  async function post(path, body, budget = timeoutMs) {
    const url = baseUrl.replace(/\/$/, '') + path;
    const res = await fetchImpl(url, {
      method: 'POST',
      headers: headersFor('POST', url),
      body: JSON.stringify(body),
      signal: AbortSignal.timeout(budget),
    });
    // A 401/403 from a remote facilitator is a CREDENTIAL failure, not an
    // outage, and it fails every payment identically. Name it, or the operator
    // reads "facilitator settle http 401" as the service being down.
    if (res.status === 401 || res.status === 403) {
      throw new Error(
        `facilitator ${path} http ${res.status}: rejected our credentials. `
        + 'Check X402_CDP_API_KEY_ID / X402_CDP_API_KEY_SECRET — every payment fails while health checks stay green.'
      );
    }
    if (!res.ok) {
      // Carry the facilitator's own message. A bare "http 400" says the request
      // was rejected but not WHY, and the why is the only actionable part when
      // a third-party facilitator disagrees with our payload shape.
      let detail = '';
      try { detail = (await res.text()).slice(0, 400).replace(/\s+/g, ' ').trim(); } catch { /* body already consumed */ }
      throw new Error(`facilitator ${path} http ${res.status}${detail ? `: ${detail}` : ''}`);
    }
    // THE ONLY FEEDBACK CHANNEL FOR DISCOVERY. A facilitator that indexes our
    // resource reports what it did with each extension in `EXTENSION-RESPONSES`
    // (base64 JSON, keyed by extension name). Nothing else tells us whether the
    // Bazaar block was accepted, rejected or ignored — settlement succeeds
    // identically either way, which is exactly how a catalog can be invisible
    // for days while every payment works. Logged, never enforced: a facilitator
    // that omits the header (CDP did not emit it as of 2026-08-20, see
    // x402-foundation/x402#2112) must not fail a payment that settled.
    const extHeader = res.headers && res.headers.get && res.headers.get('extension-responses');
    if (extHeader && logger && logger.info) {
      let decoded = null;
      try { decoded = JSON.parse(Buffer.from(extHeader, 'base64').toString('utf8')); } catch { /* logged raw below */ }
      logger.info('facilitator_extension_responses', {
        path,
        responses: decoded || String(extHeader).slice(0, 400),
      });
    }
    return res.json();
  }
  // A remote facilitator validates the payload strictly against the published
  // x402 schema; our own tolerates (and uses) our extra `resource` key. Only
  // the remote path is normalised — see toStandardV2Payload. The same call
  // carries our discovery metadata, which is how the resource gets indexed.
  const wire = authHeader ? toStandardV2Payload : ((p) => p);

  return {
    verify: (paymentPayload, paymentRequirements, discovery) =>
      post('/verify', { x402Version: 2, paymentPayload: wire(paymentPayload, discovery), paymentRequirements }),
    // `verdict` is the verify result: the EVM/CDP lane has no use for it (the
    // ANM facilitator does), so it is accepted and ignored here to keep one
    // call signature across lanes. `discovery` trails it for the same reason.
    settle: (paymentPayload, paymentRequirements, verdict, discovery) =>
      post('/settle', { x402Version: 2, paymentPayload: wire(paymentPayload, discovery), paymentRequirements }, settleBudget),
    supported: async () => {
      const url = baseUrl.replace(/\/$/, '') + '/supported';
      const res = await (fetchImpl)(url, {
        headers: authHeader ? { authorization: authHeader('GET', url) } : undefined,
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
/**
 * ANM/USD feed, built once per process. Cached inside the module (30s) so
 * building an accepts array is a memory read, not a file read per 402.
 */
// Keyed by the settings that define the feed, NOT a bare singleton: a single
// shared instance would pin the FIRST config it ever saw, so a changed path or
// staleness limit would be silently ignored for the life of the process.
const anmPriceCache = new Map();
function anmPriceFor(cfg) {
  const key = `${cfg.anmPricePath}|${cfg.anmPriceMaxAgeSeconds}`;
  let inst = anmPriceCache.get(key);
  if (!inst) {
    inst = createAnmPrice({
      path: cfg.anmPricePath,
      maxAgeSeconds: cfg.anmPriceMaxAgeSeconds,
    });
    anmPriceCache.set(key, inst);
  }
  return inst;
}

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
      // The asset's EIP-712 domain. A remote facilitator cannot rebuild the
      // signing digest without it (CDP: "missing EIP-712 domain name/version in
      // requirements.extra"); our own knows it from its network table. Constant
      // across every product, so it does not affect payment binding.
      extra: {
        name: cfg.usdcDomainName,
        version: cfg.usdcDomainVersion,
        decimals: cfg.usdcDecimals,
      },
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
  // ANM-NATIVE LANE. The payer signs a TRANSFER and pays the chain fee out of
  // their own balance, so this gateway sponsors NO gas for these settlements
  // — unlike the Base lane, where we do. That avoided cost is what funds the
  // discount; it is a real saving passed on, not a promotion.
  //
  // FAIL CLOSED ON A STALE RATE: if the ANM/USD feed is stale or unreadable
  // the lane is simply NOT OFFERED. Quoting a live payment at a dead rate is
  // the failure mode this whole module refuses; the USDC lane is unaffected,
  // so a stopped price timer degrades the offer instead of taking it down.
  if (cfg.anmLaneEnabled && cfg.anmPayTo) {
    const q = anmPriceFor(cfg).usdToNanm(route.priceUsd, { discountPercent: cfg.anmDiscountPercent });
    if (q.ok) {
      accepts.push({
        scheme: 'exact',
        // `animica:1`, NEVER `eip155:1` — this chain's id is 1 and an agent
        // reading eip155:1 would pay on Ethereum mainnet and lose the money.
        network: cfg.anmNetworkId,
        amount: q.nanm.toString(),
        asset: 'ANM',
        payTo: cfg.anmPayTo,
        maxTimeoutSeconds: cfg.maxTimeoutSeconds,
        extra: {
          native: true,
          decimals: 9,
          unit: 'nANM',
          chain_id: cfg.anmChainId,
          genesis_hash: cfg.anmGenesisHash,
          discount_percent: cfg.anmDiscountPercent,
          price_display: `${q.anm_display} ANM`,
          usd_equivalent: q.usd_after_discount,
          usd_list_price: route.priceUsd,
          rate_usd_per_anm: String(q.usd_per_anm),
          rate_source: q.source,
          rate_observed_at: q.observed_at,
          rate_side: 'bid',
          how_to_pay:
            'Sign a TRANSFER of at least `amount` nANM to payTo on Animica (chainId 1) and send the signed raw transaction as payload.rawTransaction. You pay the chain fee; we submit it. Because we sponsor no gas on this lane, it is priced ' + cfg.anmDiscountPercent + '% below the USDC price.',
        },
      });
    }
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
  // Tell a first-contact agent it can try before it buys. This rides in the
  // 402 itself rather than only in the catalog because the 402 is the moment
  // the buy/skip decision is actually made — an agent that has to go and fetch
  // the catalog to discover a free sample will usually just skip instead.
  if (route.trialPath) {
    out.free_trial = {
      endpoint: `${route.trialMethod || 'POST'} ${route.trialPath}`,
      url: l.urlFor(route.trialPath),
      price: '0',
      limit_per_day: route.trialLimitPerDay,
      note: 'Same code and same response shape as the paid endpoint. No payment, no payment headers. '
          + 'Quota is per client per UTC day; when it is spent this endpoint says so and points back here.',
    };
  }
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
  // Emitted under TWO keys with DIFFERENT dialects, because two consumers read
  // them and they do not agree on a shape.
  //
  //   `discovery`  vendor-neutral, and the one to build against: field
  //                DESCRIPTORS (bodyFields: {limit: {type, required,
  //                description}}), plus whatever `bazaarExtra` a product adds.
  //   `bazaar`     CDP's dialect, because CDP is the only consumer that reads
  //                this key: example VALUES plus a sibling `schema` that `info`
  //                is validated against. See facilitator-cdp/bazaar.js.
  //
  // THE TWO WERE IDENTICAL UNTIL 2026-08-20, and a guard test enforced it. That
  // was wrong, and provably so: CDP's own free validator
  // (POST /platform/v2/x402/validate — no auth, no payment) answers
  // `bazaar.schema: FAIL — missing`, `parse: FAIL — schema is invalid`,
  // `simulation: rejected (invalid discovery configuration)`, `index: null` for
  // every endpoint that serves the descriptor form under this key. CDP's
  // indexer CRAWLS the published 402; sending it a valid declaration only on
  // the facilitator call is not enough.
  //
  // Nothing else reads `bazaar`: x402scan registers from our OpenAPI document,
  // and 402 Index stores a self-registered record carrying no input schema at
  // all (verified against both directories' live records the same day, with all
  // 44 of our products listed). The descriptor form callers do read stays where
  // it has always been — `accepts[].outputSchema` in the v1 body — untouched.
  if (route.outputSchema || route.bazaarExtra) {
    const info = Object.assign(
      route.outputSchema ? { input: route.outputSchema.input, output: route.outputSchema.output } : {},
      route.bazaarExtra || {}
    );
    const discoveryBlock = cfg.cdpBazaarDiscoverable ? { info, discoverable: true } : { info };
    extensions.discovery = discoveryBlock;
    // `discoverable` is what makes the CDP facilitator INDEX this endpoint into
    // the Bazaar after it settles a payment for it — advertising the schema is
    // not enough on its own. Operator-authorised 2026-08-19 along with the move
    // to their facilitator; see src/facilitator-cdp/auth.js for why the two go
    // together.
    //
    // It rides on the CHALLENGE-level extension, deliberately NOT inside each
    // accepts entry. `protocol.requirementsEqual` compares accepts entries by
    // canonical JSON to decide whether a presented payment matches this
    // route's offer, so anything route-specific placed in an accepts entry
    // silently becomes part of payment binding — making a security property
    // depend on a discovery flag. Keep discovery metadata out of the money path.
    //
    // CDP's dialect, built from the same declared fields. Falls back to the
    // neutral block only if the product declares no input shape at all — an
    // unparseable declaration is still better than no declaration, because
    // `has_bazaar_extension` is itself one of the checks.
    const cdpBlock = buildCdpBazaarExtension(route);
    extensions.bazaar = cdpBlock
      ? (cfg.cdpBazaarDiscoverable ? Object.assign({ discoverable: true }, cdpBlock) : cdpBlock)
      : discoveryBlock;
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

  const evmAuthHeader = evmAuthHeaderFor(cfg);

  function facilitatorFor(network) {
    // The ANM-native lane never leaves this box: no third-party facilitator
    // settles `animica:1`, so it stays on our own facilitator regardless of
    // what the EVM lane is configured to use.
    if (network.startsWith('eip155:')) {
      return facilitatorClient(cfg.evmFacilitatorUrl, { fetchImpl, authHeader: evmAuthHeader });
    }
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

module.exports = {
  evmAuthHeaderFor, createX402Gate, facilitatorClient, buildAccepts, buildPaymentRequiredForRoute };
