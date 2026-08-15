'use strict';
/**
 * The production x402 gate for registry products. Supersedes the scaffold
 * middleware's orchestration (which is verify -> serve -> settle only) with
 * the spec's full policy set:
 *
 *   prologue    validate params (400, no payment) -> availability hook
 *               (503, NO 402 — never request payment for a service known
 *               unavailable) -> 402 with requirements -> tamper-proof match
 *               against OUR OWN accepts -> Idempotency-Key replay (stored
 *               result, no facilitator round-trip, never a second charge)
 *               -> facilitator verify.
 *
 *   execute-then-settle (cheap reads: qrng, echo) — the readiness rule from
 *               the QRNG recon: obtain the resource FIRST (a failure here
 *               charges nobody), then settle, then deliver the
 *               already-obtained result. Failed settlement withholds
 *               delivery: the payer keeps their money, we eat the compute.
 *
 *   settle-then-execute (bulk export, inference) — mandatory preSettle
 *               readiness re-check (capacity may have dropped between the
 *               402 and the paid retry; refusing pre-settle costs the payer
 *               nothing), then settle, then execute with ONE bounded retry.
 *               If the downstream still fails after settlement the payer
 *               gets a SIGNED machine-readable error receipt
 *               (src/receipts.js) and an incident row is stored for
 *               operator reconciliation/refund — money is never silently
 *               kept.
 *
 * Accounting invariants enforced here and tested in test/gateway.test.js:
 * one delivered success == exactly one facilitator settle; revenue counts
 * settled amounts only; idempotent replays, failed settlements and
 * unavailability answers never touch revenue.
 */

const crypto = require('node:crypto');

const cfgMod = require('./config');
const protocol = require('./protocol');
const { facilitatorClient, buildAccepts, buildPaymentRequiredForRoute } = require('./middleware');
const { Counter } = require('./metrics');
const { newRequestId } = require('./logging');
const { ProductError, ProductUnavailable } = require('./products/errors');

const MAX_IDEM_KEY_LEN = 200;
const DEFAULT_MAX_BODY = 64 * 1024;

function sha256Hex(s) {
  return crypto.createHash('sha256').update(s, 'utf8').digest('hex');
}

function readBody(req, maxBytes) {
  return new Promise((resolve, reject) => {
    const chunks = [];
    let size = 0;
    let tooBig = false;
    req.on('data', (c) => {
      if (tooBig) return;
      size += c.length;
      if (size > maxBytes) {
        tooBig = true;
        chunks.length = 0;
        if (size > maxBytes * 4) req.destroy();
        return;
      }
      chunks.push(c);
    });
    req.on('end', () => (tooBig ? reject(Object.assign(new Error('body too large'), { status: 413 })) : resolve(Buffer.concat(chunks))));
    req.on('error', reject);
  });
}

function createPaywall({
  cfg,
  registry,
  gatewayStore,
  receiptSigner,
  metrics,
  logger,
  fetchImpl = fetch,
  facilitatorClientFactory,
  now = Date.now,
}) {
  const makeFacilitator = facilitatorClientFactory || ((url) => facilitatorClient(url, { fetchImpl }));
  const extra = {
    idempotentReplays: new Counter('x402_idempotent_replays_total', 'Paid requests answered from the Idempotency-Key store (no new charge)'),
    incidents: new Counter('x402_incidents_total', 'Settled payments whose downstream service failed (signed receipt issued)'),
  };

  function facilitatorFor(network) {
    if (network.startsWith('eip155:')) return makeFacilitator(cfg.evmFacilitatorUrl);
    if (network.startsWith('solana:')) return makeFacilitator(cfg.svmFacilitatorUrl);
    throw new Error(`no facilitator for network ${network}`);
  }

  function send(res, status, headers, body) {
    res.writeHead(status, headers);
    res.end(body);
  }

  function sendJson(res, status, obj, headersIn) {
    send(res, status, Object.assign({ 'content-type': 'application/json' }, headersIn), JSON.stringify(obj));
  }

  function gateRouteOf(product, requestPath) {
    const route = {
      path: requestPath || product.path,
      priceUsd: product.priceUsd,
      description: product.description,
      mimeType: product.mimeType || 'application/json',
      outputSchema: product.outputSchema,
      // Identity for the 402's optional descriptive metadata (spec §2): the
      // product id/name and, through them, the documentation anchor on the
      // landing page. Purely descriptive — nothing a payer must parse.
      productId: product.id,
      productName: product.title || product.id,
    };
    // Pre-purchase honesty in the offer itself: the randomness products
    // publish the entropy source they last observed (software-fallback /
    // is_quantum:false / attested:false today) so a buyer or an indexer
    // learns the trust model from the 402, not from the paid response.
    if (typeof product.entropyDisclosure === 'function') {
      try {
        route.bazaarExtra = { entropy: product.entropyDisclosure() };
      } catch { /* a disclosure hook must never break an offer */ }
    }
    return route;
  }

  function send402(res, gateRoute, error, extraHeaders) {
    const paymentRequired = buildPaymentRequiredForRoute(gateRoute, cfg, error);
    const body = JSON.stringify(protocol.toV1Body(paymentRequired, gateRoute.outputSchema || null), null, 2);
    send(res, 402, Object.assign({
      'content-type': 'application/json',
      'payment-required': protocol.encodeHeader(paymentRequired),
    }, extraHeaders), body);
  }

  function settlementHeaders(settlement, wireVersion) {
    const settlementResponse = protocol.buildSettlementResponse(settlement);
    const v2 = protocol.encodeHeader(settlementResponse);
    const v1 = protocol.encodeHeader(Object.assign({}, settlementResponse, {
      network: cfgMod.V1_NETWORK_SLUGS[settlementResponse.network] || settlementResponse.network,
    }));
    const headers = { [protocol.HEADER_PAYMENT_RESPONSE]: v2 };
    if (wireVersion === 1) headers[protocol.HEADER_X_PAYMENT_RESPONSE] = v1;
    return { headers, v2 };
  }

  function replay(res, product, row) {
    extra.idempotentReplays.inc({ product: product.id });
    const headers = { 'idempotent-replay': 'true' };
    if (row.settlement_header) headers[protocol.HEADER_PAYMENT_RESPONSE] = row.settlement_header;
    if (row.oversize) {
      return sendJson(res, 200, {
        replay: true,
        oversize: true,
        status: 'success',
        settlement_tx: row.settlement_tx || null,
        note: 'original result exceeded the replay store cap and was delivered with the first response; the payment is settled and will not be charged again',
      }, headers);
    }
    headers['content-type'] = row.content_type || 'application/json';
    if (row.content_encoding) headers['content-encoding'] = row.content_encoding;
    return send(res, row.response_status, headers, row.body || '');
  }

  /**
   * Serialize a final product response: settlement headers on the wire's own
   * channel plus optional payment metadata injected into JSON bodies. Split
   * out from deliver() because it is the step that can THROW (a RangeError
   * over V8's max string length, a TypeError on an unserializable value) and
   * both callers need to control what a throw costs:
   *   - execute-then-settle runs it once as a PRE-FLIGHT, before settling, so
   *     an unserializable answer charges nobody;
   *   - both modes run it inside the post-settlement guard, where a throw
   *     becomes a signed receipt + incident instead of a bare 500.
   */
  function buildDelivery({ product, out, settlement, wireVersion, paymentMeta }) {
    const { headers: sHeaders, v2 } = settlementHeaders(settlement, wireVersion);
    let body = out.body;
    let contentType = (out.headers && out.headers['content-type']) || product.mimeType || 'application/json';
    if (out.bodyObj !== undefined) {
      const obj = out.bodyObj;
      if (product.injectPayment !== false) obj.payment = paymentMeta;
      body = JSON.stringify(obj);
      contentType = 'application/json';
    }
    const headers = Object.assign({}, out.headers, sHeaders, { 'content-type': contentType });
    if (out.headers && out.headers['content-encoding']) headers['content-encoding'] = out.headers['content-encoding'];
    return { status: out.status || 200, headers, body, v2 };
  }

  /**
   * A settlement-shaped placeholder for the pre-flight serialization. Same
   * field types and lengths as a real one, so the rehearsal exercises the
   * same JSON.stringify work the real delivery will do.
   */
  function placeholderSettlement(network, payer) {
    return { success: true, transaction: '0x' + '0'.repeat(64), network, payer: payer || '0x' + '0'.repeat(40) };
  }

  /**
   * Deliver a final product response and store the outcome for
   * Idempotency-Key replay. Anything that throws here happens AFTER money
   * moved, so every caller runs it inside the compensation guard.
   */
  function deliver(res, { product, out, settlement, wireVersion, paymentMeta, idemKey, fingerprint, requestPath }) {
    const d = buildDelivery({ product, out, settlement, wireVersion, paymentMeta });
    if (idemKey) {
      gatewayStore.putIdempotent({
        idemKey,
        paymentFingerprint: fingerprint,
        resource: requestPath,
        status: d.status,
        contentType: d.headers['content-type'],
        contentEncoding: d.headers['content-encoding'] || null,
        body: d.body,
        settlementHeader: d.v2,
        settlementTx: settlement.transaction || null,
      });
    }
    return send(res, d.status, d.headers, d.body);
  }

  function countSettled(product, matched, started) {
    metrics.settlementsTotal.inc({ product: product.id });
    if (matched.asset === cfg.usdcAsset) {
      metrics.revenueUsdc.inc({ product: product.id }, BigInt(matched.amount));
    }
    metrics.paymentLatencySeconds.observe({ product: product.id }, (now() - started) / 1000);
  }

  /** The full gate for one matched product route. */
  async function handleProduct(req, res, product, route, url) {
    const requestId = String(req.headers['x-request-id'] || newRequestId());
    const started = now();
    res.setHeader('x-request-id', requestId);
    const log = logger.child ? logger.child({ request_id: requestId, product: product.id }) : logger;

    if (!cfg.enabled) {
      return sendJson(res, 503, { error: 'x402_disabled', detail: 'set ANM_X402_ENABLED=1' });
    }

    const ctx = {
      method: req.method,
      url,
      query: url.searchParams,
      headers: req.headers,
      requestId,
      route,
      product,
      rawBody: null,
      json: null,
      params: {},
      pinned: {},
      settlement: null,
      paymentMeta: null,
    };

    if (req.method === 'POST' || req.method === 'PUT') {
      try {
        ctx.rawBody = await readBody(req, product.maxBodyBytes || DEFAULT_MAX_BODY);
      } catch (e) {
        return sendJson(res, e.status || 400, { error: e.status === 413 ? 'body_too_large' : 'bad_body', detail: e.message });
      }
      if (String(req.headers['content-type'] || '').includes('application/json') && ctx.rawBody.length) {
        try {
          ctx.json = JSON.parse(ctx.rawBody.toString('utf8'));
        } catch {
          ctx.json = null; // products that need JSON reject in validate()
        }
      }
    }

    // 1. Param validation: caps and shape problems answer 400 BEFORE any
    //    payment is requested — never sell a request we know is invalid.
    //
    //    EXCEPTION for discovery: a request carrying NO payment is a probe,
    //    and the 402 IS the advertisement. x402 indexers (and any agent
    //    meeting the endpoint for the first time) probe blind — no body, or a
    //    bare GET — and a 400 there makes the product invisible: it never
    //    learns the price, asset or schema because it never sees the offer.
    //    So an unpaid request always gets the 402; validation still runs
    //    before settlement for anything that actually carries payment, which
    //    is what keeps "never charge for a request we will refuse" true.
    //    A BARE PROBE is the exception, and only a bare one: no payment, no
    //    body, no query. That is what an indexer or a first-contact agent
    //    sends, and answering it 400 makes the product invisible — it never
    //    learns the price, asset or input schema, because it never sees the
    //    offer. A request that DID supply input and got it wrong still gets
    //    the 400, which is the more useful answer for a caller that is
    //    genuinely trying.
    const carriesPayment = Boolean(
      req.headers[protocol.HEADER_PAYMENT_SIGNATURE] || req.headers[protocol.HEADER_X_PAYMENT]);
    const bareProbe = !carriesPayment
      && !(ctx.rawBody && ctx.rawBody.length)
      && Array.from(url.searchParams.keys()).length === 0;
    try {
      ctx.params = product.validate ? product.validate(ctx) : {};
    } catch (e) {
      if (!(e instanceof ProductError)) throw e;
      if (!bareProbe) {
        return sendJson(res, e.status, e.body || { error: 'invalid_request', detail: e.message });
      }
      ctx.params = {}; // advertise instead: fall through to the 402
    }

    // 2. Availability hook: an unavailable product NEVER emits a 402.
    let avail;
    try {
      avail = await product.cachedAvailability();
    } catch (e) {
      avail = { available: false, reason: 'availability_check_failed', detail: e.message };
    }
    if (!avail.available) {
      log.warn && log.warn('product_unavailable', { reason: avail.reason, status: 503 });
      return sendJson(res, 503, avail.body || { error: avail.reason || `${product.id}_unavailable`, detail: avail.detail });
    }

    const gateRoute = gateRouteOf(product, route.path);
    const accepts = buildAccepts(gateRoute, cfg);
    if (accepts.length === 0) {
      log.error && log.error('no_payment_lanes', {});
      return sendJson(res, 503, { error: 'x402_unconfigured' });
    }

    // 3. Inbound payment (or the 402 that asks for one).
    let parsed;
    try {
      parsed = protocol.parsePaymentHeaders(req.headers, accepts);
    } catch (e) {
      if (e instanceof protocol.PaymentParseError) return send402(res, gateRoute, e.message);
      throw e;
    }
    if (!parsed) return send402(res, gateRoute);
    const { wireVersion, paymentPayload } = parsed;

    // 4. The client must have accepted terms WE offered, verbatim. The
    //    canonicalizer is depth-guarded (protocol.js) and client input is
    //    depth-capped at parse time, but a malformed payment must NEVER
    //    escape as a 500 — canonicalization failures are a 402 re-offer.
    let matched;
    let fingerprint;
    try {
      matched = accepts.find((a) => protocol.requirementsEqual(a, paymentPayload.accepted));
      fingerprint = sha256Hex(protocol.canonicalJson(paymentPayload));
    } catch (e) {
      return send402(res, gateRoute, 'malformed payment payload');
    }
    if (!matched) return send402(res, gateRoute, "accepted requirements do not match this server's offer");

    // The EIP-3009 authorization nonce is the deterministic cross-ledger
    // join key: incidents carry it so an operator can join a gateway
    // incident to the facilitator payments row (auth_nonce column) and to
    // the on-chain AuthorizationUsed(payer, nonce) event.
    const authNonceRaw = paymentPayload
      && paymentPayload.payload
      && paymentPayload.payload.authorization
      && paymentPayload.payload.authorization.nonce;
    const authNonce = typeof authNonceRaw === 'string' ? authNonceRaw.toLowerCase() : null;

    // 5. Idempotency-Key replay: same payment + same key that already
    //    produced a delivered outcome -> stored result, zero new charges.
    //    The stored row is bound to its resource: replaying at a DIFFERENT
    //    endpoint (possible when two products share a price and therefore
    //    byte-identical accepts) is a 409, never another product's body.
    const idemKeyRaw = req.headers['idempotency-key'];
    const idemKey = idemKeyRaw === undefined ? null : String(idemKeyRaw);
    if (idemKey !== null && (idemKey.length === 0 || idemKey.length > MAX_IDEM_KEY_LEN)) {
      return sendJson(res, 400, { error: 'bad_idempotency_key', detail: `Idempotency-Key must be 1..${MAX_IDEM_KEY_LEN} chars` });
    }
    if (idemKey) {
      const row = gatewayStore.getIdempotent(idemKey, fingerprint);
      if (row) {
        if (row.resource !== route.path) {
          log.warn && log.warn('idempotency_resource_conflict', { payment_fingerprint: fingerprint, stored_resource: row.resource, requested: route.path });
          return sendJson(res, 409, {
            error: 'idempotency_key_conflict',
            detail: 'this Idempotency-Key + payment was already used for a different resource; a payment is bound to the product it first paid for',
            resource: row.resource,
          });
        }
        log.info && log.info('idempotent_replay', { payment_fingerprint: fingerprint, status: row.response_status });
        return replay(res, product, row);
      }
    }

    // 6. Verify.
    let fc;
    try {
      fc = facilitatorFor(matched.network);
    } catch (e) {
      return send402(res, gateRoute, e.message);
    }
    let verdict;
    try {
      verdict = await fc.verify(paymentPayload, matched);
    } catch (e) {
      log.error && log.error('facilitator_verify_unreachable', { error: e.message });
      return sendJson(res, 502, { error: 'facilitator_unreachable' });
    }
    metrics.verificationsTotal.inc({ outcome: verdict.isValid ? 'valid' : (verdict.invalidReason || 'invalid') });
    if (!verdict.isValid) {
      return send402(res, gateRoute, verdict.invalidReason || 'payment invalid');
    }
    const payer = verdict.payer;
    const paymentBase = {
      network: matched.network,
      asset: matched.asset,
      amount_atomic: matched.amount,
      price_usd: product.priceUsd,
      payer: payer || undefined,
    };

    async function settleNow() {
      try {
        return await fc.settle(paymentPayload, matched);
      } catch (e) {
        // Transport failure mid-settle: the outcome is UNKNOWN (the tx may
        // land). settleFailed() below treats this exactly like a
        // returned-unknown settlement: incident + signed receipt, never a
        // bare "settlement failed" that reads as "you were not charged".
        log.error && log.error('facilitator_settle_unreachable', { error: e.message });
        return {
          success: false,
          errorReason: 'unexpected_settle_error',
          transaction: '',
          network: matched.network,
          unknown: true,
          errorDetail: e.message,
        };
      }
    }

    /**
     * A failed settlement is only a clean "you were not charged" when the
     * facilitator's answer is DEFINITIVE. Two classes are NOT definitive:
     *   - the settle call threw (transport ambiguity — flagged `unknown`);
     *   - the response names a broadcast tx hash without a definitive
     *     on-chain verdict (receipt/confirmation timeout: the facilitator's
     *     own detail says "recoverable, not final" — the transfer may land,
     *     recovery may later mark it settled and count revenue).
     * `invalid_transaction_state` with a tx hash IS definitive: the receipt
     * was read and the tx reverted — no USDC moved.
     */
    function settlementOutcomeUnknown(settlement) {
      if (settlement.unknown === true) return true;
      return Boolean(settlement.transaction) && settlement.errorReason !== 'invalid_transaction_state';
    }

    function settleFailed(settlement) {
      metrics.settlementFailuresTotal.inc({ product: product.id, reason: settlement.errorReason || 'unknown' });
      if (settlementOutcomeUnknown(settlement)) return settleUnknown(settlement);
      const { headers } = settlementHeaders(settlement, wireVersion);
      headers['payment-required'] = protocol.encodeHeader(
        buildPaymentRequiredForRoute(gateRoute, cfg, settlement.errorReason || 'settlement failed'));
      headers['content-type'] = 'application/json';
      return send(res, 402, headers,
        JSON.stringify({ x402Version: wireVersion, error: settlement.errorReason || 'settlement failed' }));
    }

    /**
     * Unknown-state settlement: the payer's USDC MAY have moved (or will).
     * Never a bare 402 — that reads as "not charged" while the transfer can
     * still confirm. Instead: open a reconciliation incident (carrying the
     * tx hash when the facilitator returned one, plus the EIP-3009 nonce as
     * the cross-ledger join key), issue a SIGNED machine-readable receipt,
     * store an idempotency reference so a same-key retry gets this pending
     * status back instead of re-running against an in-flight authorization,
     * and answer 502 telling the client to retry with the SAME payment (a
     * re-signed authorization would risk paying twice).
     */
    function settleUnknown(settlement) {
      const errText = String(settlement.errorDetail || settlement.errorReason || 'settlement outcome unknown');
      const receipt = receiptSigner.sign({
        payment_id: settlement.transaction || fingerprint,
        payment_fingerprint: fingerprint,
        resource: route.path,
        product: product.id,
        error: `settlement outcome unknown: ${errText}`,
        payer: payer || null,
        amount_atomic: matched.amount,
        asset: matched.asset,
        network: matched.network,
        settlement_tx: settlement.transaction || null,
        auth_nonce: authNonce,
      });
      const incidentId = gatewayStore.addIncident({
        paymentId: settlement.transaction || null,
        paymentFingerprint: fingerprint,
        settlementTx: settlement.transaction || null,
        payer,
        resource: route.path,
        amount: matched.amount,
        network: matched.network,
        kind: 'settle_unknown',
        error: errText,
        receipt,
        authNonce,
      });
      extra.incidents.inc({ product: product.id, kind: 'settle_unknown' });
      log.error && log.error('settlement_outcome_unknown', {
        incident_id: incidentId,
        settlement_tx: settlement.transaction || null,
        auth_nonce: authNonce,
        payer,
        amount: matched.amount,
        error: errText,
      });
      const { headers: sHeaders, v2 } = settlementHeaders(settlement, wireVersion);
      const bodyObj = {
        error: 'settlement_unknown',
        detail: 'the settlement outcome could not be confirmed; the payment MAY have settled on-chain. Do NOT sign a new authorization (that could pay twice) — retry with the SAME payment and Idempotency-Key, or reconcile with this signed receipt.',
        incident_id: incidentId,
        settlement_tx: settlement.transaction || null,
        receipt,
      };
      const body = JSON.stringify(bodyObj);
      if (idemKey) {
        gatewayStore.putIdempotent({
          idemKey, paymentFingerprint: fingerprint, resource: route.path,
          status: 502, contentType: 'application/json', body,
          settlementHeader: v2, settlementTx: settlement.transaction || null,
        });
      }
      return send(res, 502, Object.assign({ 'content-type': 'application/json' }, sHeaders), body);
    }

    /**
     * The money moved and then something after it threw — a serialization
     * failure, a full/locked DB in putIdempotent, anything. Never a bare 500:
     * that reads as "you were not charged" while the USDC is gone. Same
     * compensation as a post-settlement downstream failure: SIGNED receipt +
     * incident row for reconciliation, and a 502 that hands both to the payer.
     */
    function deliveryFailed(settlement, err) {
      const errText = String((err && err.message) || 'delivery failed after settlement');
      let receipt = null;
      let incidentId = null;
      try {
        receipt = receiptSigner.sign({
          payment_id: settlement.transaction || fingerprint,
          payment_fingerprint: fingerprint,
          resource: route.path,
          product: product.id,
          error: `delivery failed after settlement: ${errText}`,
          payer: payer || null,
          amount_atomic: matched.amount,
          asset: matched.asset,
          network: matched.network,
          settlement_tx: settlement.transaction || null,
          auth_nonce: authNonce,
        });
        incidentId = gatewayStore.addIncident({
          paymentId: settlement.transaction || null,
          paymentFingerprint: fingerprint,
          settlementTx: settlement.transaction || null,
          payer,
          resource: route.path,
          amount: matched.amount,
          network: matched.network,
          kind: 'delivery_failed',
          error: errText,
          receipt,
          authNonce,
        });
        extra.incidents.inc({ product: product.id, kind: 'delivery_failed' });
      } catch (e2) {
        // Even the compensation store can fail; the payer still gets the
        // signed receipt (pure crypto) and a truthful status code.
        log.error && log.error('incident_store_failed', { error: e2.message });
      }
      log.error && log.error('delivery_failed_after_settlement', {
        incident_id: incidentId,
        settlement_tx: settlement.transaction || null,
        auth_nonce: authNonce,
        payer,
        amount: matched.amount,
        error: errText,
      });
      const { headers: sHeaders, v2 } = settlementHeaders(settlement, wireVersion);
      if (res.headersSent) {
        // The failure happened mid-write: the status line is already gone, so
        // the receipt cannot reach the payer on this response. The incident
        // row above is the reconciliation path; just close the socket.
        return res.end();
      }
      const bodyObj = {
        error: 'delivery_failed',
        detail: 'your payment settled but the response could not be delivered; keep this signed receipt — it references the settled payment and entitles reconciliation/refund',
        incident_id: incidentId,
        settlement_tx: settlement.transaction || null,
        receipt,
      };
      const body = JSON.stringify(bodyObj);
      if (idemKey) {
        try {
          gatewayStore.putIdempotent({
            idemKey, paymentFingerprint: fingerprint, resource: route.path,
            status: 502, contentType: 'application/json', body,
            settlementHeader: v2, settlementTx: settlement.transaction || null,
          });
        } catch (e3) {
          log.error && log.error('idempotency_store_failed', { error: e3.message });
        }
      }
      return send(res, 502, Object.assign({ 'content-type': 'application/json' }, sHeaders), body);
    }

    if (product.mode === 'execute-then-settle') {
      // Obtain the resource FIRST (nothing charged on failure), settle,
      // deliver the already-obtained result.
      let out;
      try {
        out = await product.handler(ctx);
      } catch (e) {
        if (e instanceof ProductUnavailable) {
          return sendJson(res, 503, { error: e.reason, detail: e.message });
        }
        if (e instanceof ProductError) return sendJson(res, e.status, e.body || { error: 'invalid_request', detail: e.message });
        log.error && log.error('handler_failed_pre_settle', { error: e.message });
        return sendJson(res, 500, { error: 'internal_error' });
      }
      // PRE-FLIGHT: the resource is fully produced, so serialize it BEFORE
      // settling. A body that cannot be serialized (too large for V8's
      // string limit, unserializable value) must be a pre-payment failure
      // that charges nobody — not a settled payment with a bare 500.
      try {
        buildDelivery({
          product,
          out,
          settlement: placeholderSettlement(matched.network, payer),
          wireVersion,
          paymentMeta: Object.assign({}, paymentBase, { settlement_tx: '0x' + '0'.repeat(64), payer: payer || undefined }),
        });
      } catch (e) {
        log.error && log.error('response_unserializable_pre_settle', { error: e.message });
        return sendJson(res, 500, {
          error: 'response_serialization_failed',
          detail: `the response for this request could not be serialized (${e.message}); NOTHING WAS CHARGED — this check runs before settlement. If the request was very large, ask for less.`,
        });
      }
      const settlement = await settleNow();
      if (!settlement.success) return settleFailed(settlement);
      countSettled(product, matched, started);
      ctx.settlement = settlement;
      const paymentMeta = Object.assign({}, paymentBase, { settlement_tx: settlement.transaction, payer: settlement.payer || payer || undefined });
      log.info && log.info('paid_request_served', { payer, amount: matched.amount, settlement_tx: settlement.transaction, status: out.status || 200, latency_ms: now() - started });
      try {
        return deliver(res, { product, out, settlement, wireVersion, paymentMeta, idemKey, fingerprint, requestPath: route.path });
      } catch (e) {
        return deliveryFailed(settlement, e);
      }
    }

    // settle-then-execute
    // Mandatory readiness re-check immediately BEFORE settlement.
    try {
      ctx.pinned = product.preSettle ? await product.preSettle(ctx) : {};
    } catch (e) {
      if (e instanceof ProductError) return sendJson(res, e.status, e.body || { error: 'unavailable', detail: e.message });
      if (e instanceof ProductUnavailable) return sendJson(res, 503, { error: e.reason, detail: e.message });
      log.error && log.error('pre_settle_check_failed', { error: e.message });
      return sendJson(res, 503, { error: `${product.id}_unavailable`, detail: e.message });
    }

    const settlement = await settleNow();
    if (!settlement.success) return settleFailed(settlement);
    countSettled(product, matched, started);
    ctx.settlement = settlement;
    ctx.paymentMeta = Object.assign({}, paymentBase, { settlement_tx: settlement.transaction, payer: settlement.payer || payer || undefined });

    // Bounded safe retry: at most 2 attempts, and only when the failure is
    // marked retryable (network-ish, read-only work).
    let out = null;
    let lastErr = null;
    for (let attempt = 1; attempt <= 2 && !out; attempt++) {
      try {
        out = await product.handler(ctx);
      } catch (e) {
        lastErr = e;
        if (e.retryable !== true || attempt === 2) break;
        log.warn && log.warn('downstream_retry', { attempt, error: e.message });
      }
    }

    if (!out) {
      // Payment-then-service failure: signed receipt + incident row.
      const receipt = receiptSigner.sign({
        payment_id: settlement.transaction || fingerprint,
        payment_fingerprint: fingerprint,
        resource: route.path,
        product: product.id,
        error: String((lastErr && lastErr.message) || 'downstream failed'),
        payer: payer || null,
        amount_atomic: matched.amount,
        asset: matched.asset,
        network: matched.network,
      });
      const incidentId = gatewayStore.addIncident({
        paymentId: settlement.transaction || null,
        paymentFingerprint: fingerprint,
        settlementTx: settlement.transaction || null,
        payer,
        resource: route.path,
        amount: matched.amount,
        network: matched.network,
        kind: 'downstream_failed',
        error: String((lastErr && lastErr.message) || 'downstream failed'),
        receipt,
      });
      extra.incidents.inc({ product: product.id, kind: 'downstream_failed' });
      log.error && log.error('paid_service_failed', { incident_id: incidentId, settlement_tx: settlement.transaction, error: receipt.error });
      const { headers: sHeaders, v2 } = settlementHeaders(settlement, wireVersion);
      const bodyObj = {
        error: 'paid_service_failed',
        detail: 'your payment settled but the downstream service failed after retries; keep this signed receipt — it references the settled payment and entitles reconciliation/refund',
        incident_id: incidentId,
        receipt,
      };
      const body = JSON.stringify(bodyObj);
      if (idemKey) {
        gatewayStore.putIdempotent({
          idemKey, paymentFingerprint: fingerprint, resource: route.path,
          status: 502, contentType: 'application/json', body,
          settlementHeader: v2, settlementTx: settlement.transaction || null,
        });
      }
      return send(res, 502, Object.assign({ 'content-type': 'application/json' }, sHeaders), body);
    }

    log.info && log.info('paid_request_served', { payer, amount: matched.amount, settlement_tx: settlement.transaction, status: out.status || 200, latency_ms: now() - started });
    try {
      return deliver(res, { product, out, settlement, wireVersion, paymentMeta: ctx.paymentMeta, idemKey, fingerprint, requestPath: route.path });
    } catch (e) {
      // settle-then-execute cannot rehearse the serialization (the resource
      // only exists after settlement), so this guard is the whole safety net.
      return deliveryFailed(settlement, e);
    }
  }

  return {
    handleProduct,
    extraMetricsRender: () => Object.values(extra).map((c) => c.render()).join('\n') + '\n',
    extra,
  };
}

module.exports = { createPaywall };
