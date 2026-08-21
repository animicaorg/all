#!/usr/bin/env node
'use strict';
/**
 * Production gateway entry (supersedes demo-server.js, which stays as the
 * dev/smoke entry). Binds X402_GATEWAY_BIND:X402_GATEWAY_PORT (default
 * 127.0.0.1:8742 — loopback; nginx fronts it as a later runbook step).
 *
 * Routes:
 *   GET /x402                    content-negotiated discovery (unpaid):
 *                                Accept: text/html -> the landing page,
 *                                anything else -> the JSON catalog
 *                                (?format=json|html forces either)
 *   GET /.well-known/x402        the catalog, always JSON
 *   GET /x402/openapi.json       OpenAPI 3.1, generated from the registry
 *   GET /x402/stats              aggregate settlement counts (no payers)
 *   GET /x402/healthz            process liveness
 *   GET /metrics                 Prometheus text (loopback bind only)
 *   *   <product routes>         from src/products/registry.js via the
 *                                paywall (also accepts nginx-stripped
 *                                paths without the /x402 prefix)
 *   GET /x402/random/reveal/{id} FREE product route (registry.findFree):
 *                                the commit-reveal disclosure, served
 *                                outside the paywall — never a 402
 *
 * Echo stays routed ONLY outside production (X402_ENV=production disables
 * it unless X402_ENABLE_ECHO=1). Refuses to start without
 * ANM_X402_ENABLED=1, same as every entry in this app.
 */

const http = require('node:http');

const cfgMod = require('./config');
const { createMetrics, Gauge } = require('./metrics');
const { createLogger, newRequestId } = require('./logging');
const { createNodeClient } = require('./animica-node');
const { createCapacityGate } = require('./capacity');
const { createRegistry } = require('./products/registry');
const { createGatewayStore } = require('./store/gateway');
const { createScanService } = require('./scan');
const { createAnmPrice } = require('./anm-price');
const { createChainIndexStore, createChainIndexer } = require('./chain-index');
const { createReceiptSigner } = require('./receipts');
const { createPaywall } = require('./paywall');
const { renderLanding } = require('./discovery/landing');
const { buildOpenApi } = require('./discovery/openapi');
const { createSettlementStats } = require('./discovery/stats');

/**
 * Content negotiation for GET /x402. HTML only when the client actually
 * prefers it: a browser sends `text/html,...;q=0.9,*\/*;q=0.8`, while agents
 * and curl send `application/json` or `*\/*` and must keep getting the
 * catalog (a wildcard is NOT a request for a web page). `?format=json|html`
 * overrides everything — that is what monitoring scripts use.
 */
function prefersHtml(req, url) {
  const forced = url.searchParams.get('format');
  if (forced === 'json') return false;
  if (forced === 'html') return true;
  const accept = String(req.headers.accept || '');
  if (!accept) return false;
  let html = -1;
  let json = -1;
  for (const part of accept.split(',')) {
    const [typeRaw, ...paramsRaw] = part.split(';');
    const type = typeRaw.trim().toLowerCase();
    let q = 1;
    for (const p of paramsRaw) {
      const m = /^\s*q\s*=\s*([0-9.]+)\s*$/i.exec(p);
      if (m) q = Number(m[1]);
    }
    if (!Number.isFinite(q)) q = 0;
    if (type === 'text/html' || type === 'application/xhtml+xml') html = Math.max(html, q);
    else if (type === 'application/json' || type === 'application/*') json = Math.max(json, q);
  }
  return html > 0 && html >= json;
}

/**
 * Compose the gateway from resolved pieces; everything injectable so tests
 * run it against mock facilitators/node RPC with in-memory storage.
 */
function createGateway({
  cfg,
  logger,
  metrics,
  node,
  capacity,
  gatewayStore,
  chainIndex,
  chainIndexer,
  receiptSigner,
  settlementStats,
  fetchImpl = fetch,
  inferenceFetch,
  facilitatorClientFactory,
  sleep,
  now = Date.now,
  availabilityTtlMs,
} = {}) {
  cfg = cfg || cfgMod.loadGatewayConfig();
  logger = logger || createLogger({ service: 'x402-gateway' });
  metrics = metrics || createMetrics();
  node = node || createNodeClient(cfg.animicaRpcUrl, { fetchImpl });
  capacity = capacity || createCapacityGate({
    rpcUrl: cfg.animicaRpcUrl,
    wallets: cfg.inferenceWorkerWallets,
    tier: cfg.inferenceTier,
    minWorkers: cfg.priorityInferenceMinServingWorkers,
    enabled: cfg.priorityInferenceEnabled,
    fetchImpl,
    probeIntervalMs: cfg.capacityProbeIntervalMs,
    maxProbeAgeMs: cfg.capacityMaxProbeAgeMs,
    now,
    logger,
  });
  gatewayStore = gatewayStore || createGatewayStore(cfg.gatewayDbPath, { maxBodyBytes: cfg.idempotencyMaxBodyBytes });
  // Address index: its own sqlite file, its own walker. The walker is NOT
  // started here — main() starts it — so building a gateway (which is what
  // the test suite does) never puts load on the node.
  if (chainIndex === undefined) {
    chainIndex = cfg.chainIndexEnabled ? createChainIndexStore(cfg.chainIndexDbPath) : null;
  }
  if (chainIndexer === undefined && chainIndex) {
    chainIndexer = createChainIndexer({ cfg, node, store: chainIndex, logger, sleep });
  }
  receiptSigner = receiptSigner || createReceiptSigner({ secret: cfg.receiptHmacKey, now });
  if (receiptSigner.ephemeral) {
    logger.warn('receipt_key_ephemeral', {
      detail: 'X402_RECEIPT_HMAC_KEY is unset; error receipts sign with a per-boot key and stop verifying after restart. Set it in production.',
      key_id: receiptSigner.keyId,
    });
  }

  const registry = createRegistry({ cfg, node, capacity, chainIndex, gatewayStore, inferenceFetch: inferenceFetch || fetchImpl, sleep, now, availabilityTtlMs, logger });

  // ANM 402 Scan + the adoption bounty. Every route it serves is FREE, so it
  // is mounted beside the discovery endpoints rather than in the paid product
  // registry — charging for discovery would defeat what it is for.
  const scanAnmPrice = createAnmPrice({ path: cfg.anmPricePath, maxAgeSeconds: cfg.anmPriceMaxAgeSeconds, now });
  const scan = createScanService({ cfg, gatewayStore, node, fetchImpl, now, logger });

  // Dynamic pricing: peg every paid product to a small multiple (5–8x; 3x for
  // the standard inference tier) of the live Base settlement gas cost. Runs in
  // the background; on any RPC/feed failure prices stay at their static values.
  if (String(process.env.X402_DYNAMIC_PRICING || '') === '1') {
    try {
      const { startDynamicPricing } = require('./dynamic-pricing');
      startDynamicPricing({ products: registry.products, cfg, cfgMod, fetchImpl, logger });
      logger.info('x402_dynamic_pricing_enabled', {});
    } catch (e) {
      logger.warn('x402_dynamic_pricing_init_failed', { error: String(e && e.message || e) });
    }
  }
  const paywall = createPaywall({ cfg, registry, gatewayStore, receiptSigner, metrics, logger, fetchImpl, facilitatorClientFactory, now });
  // Read-only view of the facilitator's settlement ledger for GET /x402/stats.
  // Opened lazily on first use, so a gateway on a host where the facilitator
  // has never run starts fine and reports the ledger as unavailable.
  const stats = settlementStats || createSettlementStats({ cfg, registry, now });

  const servingWorkersGauge = new Gauge('x402_inference_serving_workers', 'Live serving-worker count feeding the priority-inference capacity gate');
  // The address index gates a paid product closed when it falls behind, so
  // its lag is an ops-visible number, not a log line to grep for.
  const indexHeightGauge = new Gauge('x402_chain_index_height', 'Highest fully indexed block height in the gateway address index (-1 = empty)');
  const indexLagGauge = new Gauge('x402_chain_index_lag_blocks', 'Blocks between the head the index walker last saw and the indexed height');
  const indexTickAgeGauge = new Gauge('x402_chain_index_last_tick_age_seconds', 'Seconds since the address-index walker last completed a pass');
  // The commit-reveal product is the only one that GROWS the gateway DB, so
  // its row count is an ops number (retention is X402_RANDOM_COMMIT_TTL_SECONDS).
  const commitmentsGauge = new Gauge('x402_random_commitments_stored', 'Commit-reveal commitments currently stored in the gateway DB');

  function renderMetrics() {
    servingWorkersGauge.set({}, capacity.count);
    try {
      commitmentsGauge.set({}, gatewayStore.countCommitments());
    } catch { /* an older DB file without the table must not break /metrics */ }
    let indexMetrics = '';
    if (chainIndex) {
      const st = chainIndex.getState();
      indexHeightGauge.set({}, st.indexedHeight);
      indexLagGauge.set({}, Number.isInteger(st.headHeight) ? st.headHeight - st.indexedHeight : -1);
      indexTickAgeGauge.set({}, st.lastTickMs ? Math.max(0, Math.round((now() - st.lastTickMs) / 1000)) : -1);
      // Gauge.render() has no trailing newline — join explicitly.
      indexMetrics = '\n' + [indexHeightGauge.render(), indexLagGauge.render(), indexTickAgeGauge.render()].join('\n');
    }
    return metrics.render() + paywall.extraMetricsRender() + servingWorkersGauge.render()
      + '\n' + commitmentsGauge.render() + indexMetrics + '\n';
  }

  // GET-only endpoints that live outside the product registry.
  const DISCOVERY_GET = new Set([
    '/', '/x402', '/.well-known/x402',
    '/x402/openapi.json', '/openapi.json',
    '/x402/stats', '/stats',
    '/x402/healthz', '/healthz',
    '/metrics',
  ]);

  // Methods a given path actually answers, for OPTIONS and for the Allow header
  // on a 405. Derived from the registry rather than hard-coded so a new product
  // cannot drift out of sync with what we advertise.
  function allowedMethods(path) {
    const allow = new Set();
    if (DISCOVERY_GET.has(path)) allow.add('GET');
    for (const m of ['GET', 'POST']) {
      if (registry.find(m, path) || registry.findFree(m, path)) allow.add(m);
    }
    if (allow.has('GET')) allow.add('HEAD');
    if (allow.size) allow.add('OPTIONS');
    return [...allow].sort();
  }

  function _readBodyCapped(req, maxBytes) {
  return new Promise((resolve, reject) => {
    const chunks = []; let n = 0;
    req.on('data', (c) => {
      n += c.length;
      if (n > maxBytes) { req.destroy(); reject(new Error('body too large')); return; }
      chunks.push(c);
    });
    req.on('end', () => resolve(Buffer.concat(chunks)));
    req.on('error', reject);
  });
}

async function requestHandler(req, res) {
    const url = new URL(req.url, 'http://localhost');
    const path = url.pathname.replace(/\/+$/, '') || '/';
    try {
      // The API is public and credential-free, so every response is readable
      // cross-origin. Without the expose-headers the payment challenge and the
      // settlement receipt — both header-carried — are invisible to a
      // browser-hosted agent even when the request succeeds.
      res.setHeader('access-control-allow-origin', '*');
      res.setHeader('access-control-expose-headers', 'payment-required, payment-response, x-payment-response');

      // HEAD is GET without a body (RFC 9110 9.3.2): same status, same headers.
      // Indexers and uptime monitors probe with HEAD before they ever send a
      // GET, and answering 404 there made every POST-only product read as dead
      // to a crawler — 115 such 404s in the current access log, all from
      // discovery services. Serve the identical response and drop the body.
      const isHead = req.method === 'HEAD';
      if (isHead) {
        const endBodiless = res.end.bind(res);
        res.end = () => endBodiless();
        req.method = 'GET';
      }

      // OPTIONS answers "what can I do here" without spending a request on a
      // guess. Browser-hosted agents also need it for CORS preflight before
      // they may read a 402 challenge at all.
      if (req.method === 'OPTIONS') {
        const allow = allowedMethods(path);
        if (!allow.length) {
          res.writeHead(404, { 'content-type': 'application/json' });
          return res.end(JSON.stringify({
            error: 'not_found',
            discovery: ['/x402', '/.well-known/x402', '/x402/openapi.json', '/x402/stats'],
          }));
        }
        res.writeHead(204, {
          allow: allow.join(', '),
          'access-control-allow-origin': '*',
          'access-control-allow-methods': allow.join(', '),
          // The payment headers are the whole protocol; a preflight that omits
          // them makes the 402 unreadable from a browser.
          'access-control-allow-headers': 'content-type, payment-signature, x-payment, x-request-id',
          'access-control-expose-headers': 'payment-required, payment-response, x-payment-response',
          'access-control-max-age': '86400',
        });
        return res.end();
      }
      // Catalog aliases. These are not invented: they are the paths x402
      // indexers and crawlers ACTUALLY request against this host, taken from
      // the nginx 404 log (x402-services.json, /x402.json, /api/x402). A 404
      // on a discovery path is the same failure as the old HEAD-probe 404s —
      // the crawler concludes there is nothing here and moves on, and we stay
      // invisible to exactly the audience we want.
      const CATALOG_ALIASES = new Set([
        '/x402', '/', '/x402.json', '/api/x402',
        '/.well-known/x402', '/.well-known/x402.json', '/.well-known/x402-services.json',
      ]);
      if (req.method === 'GET' && CATALOG_ALIASES.has(path)) {
        const catalog = await registry.catalog();
        // /.well-known/ is a machine location by definition — it never
        // negotiates. /x402 does: a browser (Accept: text/html) gets the
        // landing page, an agent or curl gets the catalog. ?format= wins over
        // both, which is what the health-check script and humans use.
        // `.json` and the well-known path always return JSON — some discovery
        // indexers probe `/.well-known/x402.json` specifically, so it must not
        // negotiate to HTML.
        const isWellKnown = path.startsWith('/.well-known/') || path === '/x402.json' || path === '/api/x402';
        const wantHtml = !isWellKnown && prefersHtml(req, url);
        if (wantHtml) {
          const html = renderLanding({ cfg, catalog, products: registry.products, now });
          res.writeHead(200, {
            'content-type': 'text/html; charset=utf-8',
            'cache-control': 'public, max-age=60',
            // The page is self-contained: no scripts, no external requests.
            'content-security-policy': "default-src 'none'; style-src 'unsafe-inline'; img-src 'self' data:; base-uri 'none'; form-action 'none'",
            'x-content-type-options': 'nosniff',
          });
          return res.end(html);
        }
        const body = JSON.stringify(catalog, null, 2);
        res.writeHead(200, { 'content-type': 'application/json', 'cache-control': 'public, max-age=15' });
        return res.end(body);
      }
      if (req.method === 'GET' && (path === '/x402/openapi.json' || path === '/openapi.json')) {
        const catalog = await registry.catalog();
        const doc = buildOpenApi({ cfg, catalog, products: registry.products });
        res.writeHead(200, { 'content-type': 'application/json', 'cache-control': 'public, max-age=60' });
        return res.end(JSON.stringify(doc, null, 2));
      }
      // Directory + bounty (all free). Placed before the product registry so a
      // /x402/scan path can never be mistaken for a paid route.
      if (path.startsWith('/x402/scan') || path.startsWith('/x402/bounty')) {
        if (await scan.handle(req, res, url, path, scanAnmPrice)) return undefined;
      }
      if (req.method === 'GET' && (path === '/x402/stats' || path === '/stats')) {
        const body = JSON.stringify(stats.snapshot(), null, 2);
        res.writeHead(200, { 'content-type': 'application/json', 'cache-control': 'public, max-age=30' });
        return res.end(body);
      }
      if (req.method === 'GET' && (path === '/x402/healthz' || path === '/healthz')) {
        res.writeHead(200, { 'content-type': 'application/json' });
        return res.end(JSON.stringify({ ok: true, service: 'x402-gateway', env: cfg.env }));
      }
      if (req.method === 'GET' && path === '/metrics') {
        const body = renderMetrics();
        res.writeHead(200, { 'content-type': 'text/plain; version=0.0.4' });
        return res.end(body);
      }
      const match = registry.find(req.method, path);
      if (match) {
        // A GET resolved onto a POST-only product is a discovery probe: it may
        // read the 402 offer, never buy. Paying over the wrong method would
        // settle against a request the handler cannot serve.
        if (match.probeOnly
            && (req.headers['payment-signature'] || req.headers['x-payment'])) {
          res.writeHead(405, { 'content-type': 'application/json', allow: 'POST' });
          return res.end(JSON.stringify({
            error: 'method_not_allowed',
            detail: `${path} is POST-only; GET returns the payment offer for discovery`,
          }));
        }
        return await paywall.handleProduct(req, res, match.product, match.route, url);
      }
      // FREE product routes (commit-reveal disclosure): no paywall, no 402,
      // no facilitator round-trip — an audit surface that costs nothing is
      // the entire point of publishing a commitment.
      const free = registry.findFree(req.method, path);
      if (free) {
        // Free/trial POST routes bypass the paywall, which is where ctx.json is
        // built. Without parsing the body here, a POST product's validate()
        // (which reads ctx.json) 400s on every trial. Parse it for POST/PUT so
        // the free-trial hook actually works for inference/chain/media.
        let rawBody = null, json = null;
        if (req.method === 'POST' || req.method === 'PUT') {
          try {
            // A free route may need a bigger body than the product it hangs
            // off: /x402/crawl/licence/verify carries a 3.3KB ML-DSA-65
            // signature plus a 2KB public key (~11KB of JSON) while its parent
            // pass product caps at 4KB. Inheriting the parent's cap made every
            // real verification 502 — the endpoint worked perfectly on a small
            // hand-made payload and failed on every genuine one.
            rawBody = await _readBodyCapped(req, (free.route && free.route.maxBodyBytes)
              || (free.product && free.product.maxBodyBytes) || (64 * 1024));
            if (String(req.headers['content-type'] || '').includes('application/json') && rawBody && rawBody.length) {
              try { json = JSON.parse(rawBody.toString('utf8')); } catch (_e) { json = null; }
            }
          } catch (_e) { rawBody = null; json = null; }
        }
        const out = await free.route.handler({
          method: req.method,
          url,
          query: url.searchParams,
          headers: req.headers,
          // Free routes may be quota-limited per client, and clientKey falls
          // back to the socket address when no proxy header is present.
          remoteAddress: (req.socket && req.socket.remoteAddress) || null,
          params: free.params,
          product: free.product,
          rawBody,
          json,
        });
        const headers = Object.assign({ 'content-type': out.bodyObj !== undefined ? 'application/json' : (out.contentType || 'application/json') }, out.headers);
        res.writeHead(out.status || 200, headers);
        return res.end(out.bodyObj !== undefined ? JSON.stringify(out.bodyObj) : (out.body || ''));
      }
      // A free route that exists under a DIFFERENT method answers 405 with
      // Allow, never 404. (Observed: a crawler GET on /x402/qrng/bulk/trial,
      // a POST-only trial, got a 404 and would reasonably conclude the trial
      // does not exist.)
      const freeOther = registry.findFreeAnyMethod && registry.findFreeAnyMethod(path);
      if (freeOther) {
        res.writeHead(405, { 'content-type': 'application/json', allow: freeOther.route.method });
        return res.end(JSON.stringify({
          error: 'method_not_allowed',
          detail: `this endpoint exists but takes ${freeOther.route.method}, not ${req.method}`,
          endpoint: `${freeOther.route.method} ${freeOther.route.path}`,
          description: freeOther.route.description || undefined,
          catalog: '/.well-known/x402',
        }, null, 2));
      }

      // RETIRED routes get 410 with a forwarding pointer, not a bare 404.
      // The demo echo was registered with an x402 indexer before the real
      // products existed, so uptime monitors and cached agent configs still
      // probe it — and a 404 tells them nothing except "broken", which is what
      // a trust monitor then publishes about the whole origin. 410 is the
      // status that means "deliberately gone", and the body hands the caller
      // the live catalog so a machine can re-target itself without a human.
      //
      // THE BODY IS NOT ENOUGH (2026-08-20). x402-observer polls this route
      // ~once per sweep and has done so for days, which means nothing it read
      // in our JSON body persuaded it to stop — uptime monitors classify on
      // status and headers, and most never parse an error body at all. So the
      // retirement is also stated in the standard header vocabulary a monitor
      // already understands: Deprecation (RFC 9745) and Sunset (RFC 8594) say
      // WHEN, and the Link relations say WHERE TO GO INSTEAD. Together they
      // are the machine-readable form of "this is intentional, here is the
      // successor" — the same claim the body makes, in the place a crawler
      // actually looks. Cache-Control lets a well-behaved monitor stop asking.
      const RETIRED = {
        '/x402/paid/echo': {
          reason: 'the echo route was a settlement smoke test, never a product',
          // The instant it stopped being served in production: the release
          // that defaulted X402_ENABLE_ECHO off. Not the date we started
          // saying so — a Sunset in the future would be a lie about a route
          // that is already gone.
          goneAtMs: Date.UTC(2026, 7, 15, 1, 3, 28),
          successor: '/x402/qrng/draw',
        },
      };
      const retired = RETIRED[path] || RETIRED[`/x402${path}`];
      if (retired) {
        const goneAtSec = Math.floor(retired.goneAtMs / 1000);
        res.writeHead(410, {
          'content-type': 'application/json',
          // RFC 9745: a structured-field Date, i.e. "@" + unix seconds.
          deprecation: `@${goneAtSec}`,
          // RFC 8594: an IMF-fixdate in the past — already withdrawn.
          sunset: new Date(retired.goneAtMs).toUTCString(),
          link: [
            `<${retired.successor}>; rel="successor-version"`,
            '</.well-known/x402>; rel="index"',
            '</x402/openapi.json>; rel="service-desc"',
          ].join(', '),
          'cache-control': 'public, max-age=86400',
        });
        return res.end(JSON.stringify({
          error: 'gone',
          detail: retired.reason,
          gone_at: new Date(retired.goneAtMs).toISOString(),
          catalog: '/.well-known/x402',
          openapi: '/x402/openapi.json',
          suggested: retired.successor,
        }));
      }
      res.writeHead(404, { 'content-type': 'application/json' });
      return res.end(JSON.stringify({
        error: 'not_found',
        discovery: ['/x402', '/.well-known/x402', '/x402/openapi.json', '/x402/stats'],
      }));
    } catch (e) {
      logger.error('request_failed', { request_id: String(req.headers['x-request-id'] || newRequestId()), path, error: e.message });
      if (!res.headersSent) res.writeHead(500, { 'content-type': 'application/json' });
      return res.end(JSON.stringify({ error: 'internal_error' }));
    }
  }

  const server = http.createServer(requestHandler);
  server.requestTimeout = Math.max(cfg.inferenceTimeoutMs + 30_000, 120_000);
  server.headersTimeout = 15_000;

  return {
    cfg, server, registry, paywall, capacity, node, gatewayStore, chainIndex, chainIndexer,
    receiptSigner, metrics, renderMetrics, logger, stats,
  };
}

function main() {
  if (process.env.ANM_X402_ENABLED !== '1') {
    console.error('refusing to start: set ANM_X402_ENABLED=1');
    process.exit(1);
  }
  let gw;
  try {
    gw = createGateway();
  } catch (e) {
    // Config errors must never hot-loop with secrets in argv — print the
    // reason (never the values) and exit non-zero.
    console.error(`[x402-gateway] refusing to start: ${e.message}`);
    process.exit(1);
  }
  const { cfg, server, registry, capacity, gatewayStore, chainIndex, chainIndexer, logger } = gw;

  const pruned = gatewayStore.pruneIdempotency(cfg.idempotencyTtlSeconds);
  if (pruned) logger.info('idempotency_pruned', { rows: pruned });

  // Sealed/revealed commitments are kept for X402_RANDOM_COMMIT_TTL_SECONDS
  // (90 days). The retention window is stated in the reveal 404 body — a
  // reveal route that quietly forgets is worse than one that says so.
  const prunedCommits = gatewayStore.pruneCommitments(cfg.randomCommitTtlSeconds);
  if (prunedCommits) logger.info('commitments_pruned', { rows: prunedCommits, ttl_seconds: cfg.randomCommitTtlSeconds });

  capacity.start(); // no-op unless PRIORITY_INFERENCE_ENABLED=1

  // The address-index walker runs ONLY in a real process — never from
  // createGateway() — so no test or import ever walks the chain. Backfill of
  // the whole chain measured ~5-7 min; the history product stays
  // available:false (503, never a 402) until it is caught up.
  if (chainIndexer) {
    const st = chainIndex.getState();
    logger.info('chain_index_start', {
      db: cfg.chainIndexDbPath,
      indexed_height: st.indexedHeight,
      chunk_blocks: cfg.chainIndexChunkBlocks,
      max_lag_blocks: cfg.chainIndexMaxLagBlocks,
    });
    chainIndexer.start();
  }

  server.listen(cfg.gatewayPort, cfg.gatewayBind, () => {
    logger.info('listening', {
      bind: cfg.gatewayBind,
      port: cfg.gatewayPort,
      env: cfg.env,
      facilitator_mode: cfg.facilitatorMode,
      products: registry.products.filter((p) => p.enabled).map((p) => `${p.id}@$${p.priceUsd}`),
    });
  });
}

if (require.main === module) {
  main();
}

module.exports = { createGateway };
