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
    return {
      path: requestPath || product.path,
      priceUsd: product.priceUsd,
      description: product.description,
      mimeType: product.mimeType || 'application/json',
      outputSchema: product.outputSchema,
    };
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
   * Deliver a final product response: settlement headers on the wire's own
   * channel, optional payment metadata injected into JSON bodies, and the
   * outcome stored for Idempotency-Key replay.
   */
  function deliver(res, { product, out, settlement, wireVersion, paymentMeta, idemKey, fingerprint, requestPath }) {
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
    if (idemKey) {
      gatewayStore.putIdempotent({
        idemKey,
        paymentFingerprint: fingerprint,
        resource: requestPath,
        status: out.status || 200,
        contentType,
        contentEncoding: headers['content-encoding'] || null,
        body,
        settlementHeader: v2,
        settlementTx: settlement.transaction || null,
      });
    }
    return send(res, out.status || 200, headers, body);
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
    try {
      ctx.params = product.validate ? product.validate(ctx) : {};
    } catch (e) {
      if (e instanceof ProductError) return sendJson(res, e.status, e.body || { error: 'invalid_request', detail: e.message });
      throw e;
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

    // 4. The client must have accepted terms WE offered, verbatim.
    const matched = accepts.find((a) => protocol.requirementsEqual(a, paymentPayload.accepted));
    if (!matched) return send402(res, gateRoute, "accepted requirements do not match this server's offer");

    // 5. Idempotency-Key replay: same payment + same key that already
    //    produced a delivered outcome -> stored result, zero new charges.
    const idemKeyRaw = req.headers['idempotency-key'];
    const idemKey = idemKeyRaw === undefined ? null : String(idemKeyRaw);
    if (idemKey !== null && (idemKey.length === 0 || idemKey.length > MAX_IDEM_KEY_LEN)) {
      return sendJson(res, 400, { error: 'bad_idempotency_key', detail: `Idempotency-Key must be 1..${MAX_IDEM_KEY_LEN} chars` });
    }
    const fingerprint = sha256Hex(protocol.canonicalJson(paymentPayload));
    if (idemKey) {
      const row = gatewayStore.getIdempotent(idemKey, fingerprint);
      if (row) {
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
        // land). Withhold the resource, tell the client, and open an
        // incident so the operator reconciles against the chain/DB.
        log.error && log.error('facilitator_settle_unreachable', { error: e.message });
        gatewayStore.addIncident({
          paymentFingerprint: fingerprint,
          payer,
          resource: route.path,
          amount: matched.amount,
          network: matched.network,
          kind: 'settle_unknown',
          error: e.message,
        });
        extra.incidents.inc({ product: product.id, kind: 'settle_unknown' });
        return { success: false, errorReason: 'unexpected_settle_error', transaction: '', network: matched.network };
      }
    }

    function settleFailed(settlement) {
      metrics.settlementFailuresTotal.inc({ product: product.id, reason: settlement.errorReason || 'unknown' });
      const { headers } = settlementHeaders(settlement, wireVersion);
      headers['payment-required'] = protocol.encodeHeader(
        buildPaymentRequiredForRoute(gateRoute, cfg, settlement.errorReason || 'settlement failed'));
      headers['content-type'] = 'application/json';
      return send(res, 402, headers,
        JSON.stringify({ x402Version: wireVersion, error: settlement.errorReason || 'settlement failed' }));
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
      const settlement = await settleNow();
      if (!settlement.success) return settleFailed(settlement);
      countSettled(product, matched, started);
      ctx.settlement = settlement;
      const paymentMeta = Object.assign({}, paymentBase, { settlement_tx: settlement.transaction, payer: settlement.payer || payer || undefined });
      log.info && log.info('paid_request_served', { payer, amount: matched.amount, settlement_tx: settlement.transaction, status: out.status || 200, latency_ms: now() - started });
      return deliver(res, { product, out, settlement, wireVersion, paymentMeta, idemKey, fingerprint, requestPath: route.path });
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
    return deliver(res, { product, out, settlement, wireVersion, paymentMeta: ctx.paymentMeta, idemKey, fingerprint, requestPath: route.path });
  }

  return {
    handleProduct,
    extraMetricsRender: () => Object.values(extra).map((c) => c.render()).join('\n') + '\n',
    extra,
  };
}

module.exports = { createPaywall };
