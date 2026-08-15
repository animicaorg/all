'use strict';
/**
 * Config-driven product registry. Every paid product is one object:
 *
 *   { id, title, description, path (canonical catalog path),
 *     routes: [{method, path}], priceUsd (USD decimal string -> atomic via
 *     config.usdToUsdcAtomic at 402 time), enabled (bool — disabled
 *     products are not routed and not listed, EXCEPT products marked
 *     listedEvenWhenUnavailable, whose whole point is a truthful
 *     available:false), mode ('execute-then-settle' | 'settle-then-execute'),
 *     mimeType, outputSchema (bazaar/v1 discovery), maxBodyBytes?,
 *     injectPayment?, availability() -> {available, reason?, detail?,
 *     body?}, validate(ctx)?, preSettle(ctx)?, handler(ctx) }
 *
 * availability() results are memoized briefly so catalog scrapers cannot
 * hammer the beacon/node through us.
 */

const cfgMod = require('../config');
const { createQrngProduct, createRandomnessSource } = require('./qrng');
const { createRandomProducts } = require('./random');
const { createBulkChainProduct } = require('./bulk-chain');
const { createChainAddressHistoryProduct } = require('./chain-address-history');
const { createChainBalancesProduct } = require('./chain-balances');
const { createPriorityInferenceProduct } = require('./priority-inference');
const { createIndexHealth } = require('../chain-index');

/** Memoize an async fn for ttlMs (shared across callers, error-transparent). */
function memoAsync(fn, ttlMs, now = Date.now) {
  let at = 0;
  let value;
  let pending = null;
  return async function memoized() {
    if (pending) return pending;
    if (now() - at < ttlMs && value !== undefined) return value;
    pending = Promise.resolve()
      .then(fn)
      .then((v) => { value = v; at = now(); pending = null; return v; })
      .catch((e) => { pending = null; throw e; });
    return pending;
  };
}

/** Development-only echo — the settlement smoke-test marker (spec: keep). */
function createEchoProduct({ cfg }) {
  return {
    id: 'echo',
    title: 'Echo (development only)',
    description:
      'DEVELOPMENT-ONLY settlement smoke test: echoes the request back. Disabled when X402_ENV=production unless X402_ENABLE_ECHO=1. Not a real product.',
    path: '/x402/paid/echo',
    routes: [
      { method: 'GET', path: '/x402/paid/echo' },
      { method: 'POST', path: '/x402/paid/echo' },
    ],
    priceUsd: '0.005',
    enabled: cfg.echoEnabled,
    mode: 'execute-then-settle',
    mimeType: 'application/json',
    outputSchema: {
      input: {
        type: 'http',
        method: 'GET',
        queryParams: { msg: { type: 'string', description: 'any text; echoed back along with all query parameters' } },
      },
      output: { type: 'json', description: 'JSON object echoing the request method and query parameters (development-only)' },
    },
    async availability() {
      return { available: true };
    },
    async handler(ctx) {
      return {
        status: 200,
        bodyObj: {
          echo: { method: ctx.method, path: ctx.url.pathname, query: Object.fromEntries(ctx.query) },
          development_only: true,
        },
      };
    },
  };
}

function createRegistry({ cfg, node, capacity, chainIndex, gatewayStore, inferenceFetch, sleep, now = Date.now, availabilityTtlMs = 5000 }) {
  // ONE randomness source for the whole family: one node call path, one
  // health-gated readiness probe (memoized for the same TTL as availability,
  // so a catalog scrape cannot fan out into six node calls).
  const randomnessSource = createRandomnessSource({ cfg, node, probeTtlMs: availabilityTtlMs, now });

  const products = [
    createEchoProduct({ cfg }),
    createQrngProduct({ cfg, node, source: randomnessSource }),
    createBulkChainProduct({ cfg, node, sleep }),
    // The address-history product cannot exist without its index; without an
    // index the route is simply absent rather than a paid endpoint that 503s.
    ...(chainIndex
      ? [createChainAddressHistoryProduct({
        cfg,
        node,
        chainIndex,
        indexHealth: createIndexHealth({ cfg, store: chainIndex, node, now }),
      })]
      : []),
    createChainBalancesProduct({ cfg, node }),
    createPriorityInferenceProduct({ cfg, capacity, fetchImpl: inferenceFetch }),
  ];

  // A. The randomness family (int / shuffle / pick / bulk / commit-reveal):
  // all five derive from the SAME single verified draw path and share the
  // readiness probe above, so an unhealthy entropy source fails the whole
  // family closed BEFORE any payment is requested. The commit product needs
  // the gateway DB for its sealed secrets and its free public reveal route.
  if (gatewayStore) {
    products.push(...createRandomProducts({ cfg, node, source: randomnessSource, gatewayStore, now }));
  }

  for (const p of products) {
    p.priceAtomic = cfgMod.usdToUsdcAtomic(p.priceUsd);
    p.cachedAvailability = memoAsync(() => p.availability(), availabilityTtlMs, now);
  }

  const routeIndex = new Map(); // 'METHOD path' -> {product, route}
  for (const p of products) {
    if (!p.enabled) continue;
    for (const r of p.routes) {
      routeIndex.set(`${r.method} ${r.path}`, { product: p, route: r });
    }
  }

  function find(method, pathname) {
    return (
      routeIndex.get(`${method} ${pathname}`) ||
      // nginx may strip the /x402 prefix when proxying — accept both forms.
      routeIndex.get(`${method} /x402${pathname}`) ||
      null
    );
  }

  /**
   * FREE, unpaid product routes (pattern-matched, so ids can ride in the
   * path). Today that is the commit-reveal disclosure: the whole point of a
   * commitment is that ANYONE can audit it, so the reveal must never sit
   * behind a paywall. These bypass the paywall entirely — they take no
   * payment, emit no 402 and must stay side-effect-light. Only routes of
   * ENABLED products are indexed.
   */
  const freeRoutes = [];
  for (const p of products) {
    if (!p.enabled || !Array.isArray(p.freeRoutes)) continue;
    for (const r of p.freeRoutes) freeRoutes.push({ product: p, route: r });
  }

  function findFree(method, pathname) {
    for (const entry of freeRoutes) {
      if (entry.route.method !== method) continue;
      // nginx may strip the /x402 prefix when proxying — accept both forms.
      const params = entry.route.match(pathname) || entry.route.match(`/x402${pathname}`);
      if (params) return { product: entry.product, route: entry.route, params };
    }
    return null;
  }

  /**
   * Public discovery catalog (GET /x402 and /.well-known/x402):
   * {name, network, asset, products: [{id, path, price, description,
   * available}]} per spec, plus schema/endpoint extras indexers use.
   * `available` is LIVE from each product's availability hook.
   */
  async function catalog() {
    const out = {
      x402Version: 2,
      name: cfg.serviceName,
      network: cfg.networkEvm,
      asset: cfg.usdcAsset,
      scheme: 'exact',
      products: [],
      updated_at: new Date(now()).toISOString(),
    };
    for (const p of products) {
      if (!p.enabled && !p.listedEvenWhenUnavailable) continue;
      let avail;
      try {
        avail = p.enabled ? await p.cachedAvailability() : { available: false, reason: 'disabled' };
      } catch (e) {
        avail = { available: false, reason: 'availability_check_failed', detail: e.message };
      }
      const entry = {
        id: p.id,
        path: p.path,
        price: p.priceUsd,
        price_atomic: p.priceAtomic,
        currency: 'USDC',
        description: p.description,
        available: Boolean(avail.available),
        endpoints: p.routes.map((r) => `${r.method} ${r.path}`),
        mimeType: p.mimeType || 'application/json',
      };
      if (!avail.available && avail.reason) entry.unavailable_reason = avail.reason;
      if (Array.isArray(p.freeRoutes) && p.freeRoutes.length) {
        // Advertised so an indexer (and a buyer) can see that part of this
        // product costs nothing — e.g. the commit-reveal disclosure.
        entry.free_endpoints = p.freeRoutes.map((r) => ({
          endpoint: `${r.method} ${r.path}`,
          price: '0',
          description: r.description || '',
        }));
      }
      if (p.outputSchema) {
        entry.outputSchema = p.outputSchema; // v1/Bazaar-style {input, output}
      }
      out.products.push(entry);
    }
    return out;
  }

  return { products, find, findFree, freeRoutes, catalog };
}

module.exports = { createRegistry, memoAsync };
