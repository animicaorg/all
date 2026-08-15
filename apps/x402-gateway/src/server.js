#!/usr/bin/env node
'use strict';
/**
 * Production gateway entry (supersedes demo-server.js, which stays as the
 * dev/smoke entry). Binds X402_GATEWAY_BIND:X402_GATEWAY_PORT (default
 * 127.0.0.1:8742 — loopback; nginx fronts it as a later runbook step).
 *
 * Routes:
 *   GET /x402                    public discovery catalog (unpaid)
 *   GET /.well-known/x402        same catalog (ecosystem convention)
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
const { createChainIndexStore, createChainIndexer } = require('./chain-index');
const { createReceiptSigner } = require('./receipts');
const { createPaywall } = require('./paywall');

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

  const registry = createRegistry({ cfg, node, capacity, chainIndex, gatewayStore, inferenceFetch: inferenceFetch || fetchImpl, sleep, now, availabilityTtlMs });
  const paywall = createPaywall({ cfg, registry, gatewayStore, receiptSigner, metrics, logger, fetchImpl, facilitatorClientFactory, now });

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

  async function requestHandler(req, res) {
    const url = new URL(req.url, 'http://localhost');
    const path = url.pathname.replace(/\/+$/, '') || '/';
    try {
      if (req.method === 'GET' && (path === '/x402' || path === '/.well-known/x402' || path === '/')) {
        const body = JSON.stringify(await registry.catalog(), null, 2);
        res.writeHead(200, { 'content-type': 'application/json', 'cache-control': 'public, max-age=15' });
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
        return await paywall.handleProduct(req, res, match.product, match.route, url);
      }
      // FREE product routes (commit-reveal disclosure): no paywall, no 402,
      // no facilitator round-trip — an audit surface that costs nothing is
      // the entire point of publishing a commitment.
      const free = registry.findFree(req.method, path);
      if (free) {
        const out = await free.route.handler({
          method: req.method,
          url,
          query: url.searchParams,
          headers: req.headers,
          params: free.params,
          product: free.product,
        });
        const headers = Object.assign({ 'content-type': out.bodyObj !== undefined ? 'application/json' : (out.contentType || 'application/json') }, out.headers);
        res.writeHead(out.status || 200, headers);
        return res.end(out.bodyObj !== undefined ? JSON.stringify(out.bodyObj) : (out.body || ''));
      }
      res.writeHead(404, { 'content-type': 'application/json' });
      return res.end(JSON.stringify({ error: 'not_found', discovery: ['/x402', '/.well-known/x402'] }));
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
    receiptSigner, metrics, renderMetrics, logger,
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
