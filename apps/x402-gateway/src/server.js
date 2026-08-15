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
  receiptSigner = receiptSigner || createReceiptSigner({ secret: cfg.receiptHmacKey, now });
  if (receiptSigner.ephemeral) {
    logger.warn('receipt_key_ephemeral', {
      detail: 'X402_RECEIPT_HMAC_KEY is unset; error receipts sign with a per-boot key and stop verifying after restart. Set it in production.',
      key_id: receiptSigner.keyId,
    });
  }

  const registry = createRegistry({ cfg, node, capacity, inferenceFetch: inferenceFetch || fetchImpl, sleep, now, availabilityTtlMs });
  const paywall = createPaywall({ cfg, registry, gatewayStore, receiptSigner, metrics, logger, fetchImpl, facilitatorClientFactory, now });

  const servingWorkersGauge = new Gauge('x402_inference_serving_workers', 'Live serving-worker count feeding the priority-inference capacity gate');

  function renderMetrics() {
    servingWorkersGauge.set({}, capacity.count);
    return metrics.render() + paywall.extraMetricsRender() + servingWorkersGauge.render() + '\n';
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

  return { cfg, server, registry, paywall, capacity, node, gatewayStore, receiptSigner, metrics, renderMetrics, logger };
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
  const { cfg, server, registry, capacity, gatewayStore, logger } = gw;

  const pruned = gatewayStore.pruneIdempotency(cfg.idempotencyTtlSeconds);
  if (pruned) logger.info('idempotency_pruned', { rows: pruned });

  capacity.start(); // no-op unless PRIORITY_INFERENCE_ENABLED=1

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
