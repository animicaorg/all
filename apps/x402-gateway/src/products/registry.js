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
const { buildAccepts } = require('../middleware');
const { toDiscoveryAccepts } = require('../protocol');
const { identity, networkFacts, links } = require('../discovery/links');
const { createQrngProduct, createRandomnessSource } = require('./qrng');
const { createRandomProducts } = require('./random');
const { createBulkChainProduct } = require('./bulk-chain');
const { createChainAddressHistoryProduct } = require('./chain-address-history');
const { createChainBalancesProduct } = require('./chain-balances');
const { createPriorityInferenceProduct, createTierStandardsProduct } = require('./priority-inference');
const { createMediaProducts } = require('./media');
const { createTrialRoute } = require('./trial');
const { createCreditsProduct, createCreditsBalanceRoute } = require('./credits');
const { createFetchProduct } = require('./web');
const { createFreeCrawlRoute } = require('./crawl-free');
const { createCrawlGate } = require('./crawl-gate');
const { createCrawlTriage } = require('./crawl-triage');
const { createCrawlLicence } = require('./crawl-licence');
const { createAicfEngine } = require('./aicf');
const {
  createNotarizeProduct, createNotarizeVerifyRoute,
  createBlobProduct, createBlobGetRoute,
} = require('./notary');
const { createEmbeddingsProduct } = require('./embeddings');
const { createAskUrlProduct } = require('./ask-url');
const {
  createOracleProduct, createSnapshotProduct, createMempoolProduct,
} = require('./data-feeds');
const { createAnmPrice } = require('../anm-price');
const { createLeaseProduct } = require('./lease');
const { createPqVerifyProduct } = require('./pq');
const {
  createForecastProduct, createForecastRecordRoute, createCalibrationRoute,
  resolveOpenForecasts,
} = require('./forecast');
const { createExecuteProduct } = require('./execute');
const { createUtilityProducts } = require('./utility');
const { createGeoAuditProduct } = require('./geo');
const { createGeoFixProduct } = require('./geo-fix');
const { createMeshFindProduct, createMeshProbeProduct, createMeshIndexCache } = require('./mesh');
const { createHarvester } = require('./mesh-harvest');
const { createSolveProduct } = require('./solve');
const { createAnalyticsProducts } = require('./analytics');
const { createBuyProduct } = require('./buy');
const { createIndexHealth } = require('../chain-index');

// Module-scoped so repeated buildRegistry() calls (tests, hot reload) cannot
// stack up background probes against the marketplace. unref'd so it never
// holds the process open.
let mediaProbeTimer = null;
// Module-scoped for the same reason: repeated buildRegistry() calls in tests
// must not stack up background scoring sweeps against the market API.
let forecastSweepTimer = null;
// Same reason again: repeated buildRegistry() calls must not stack up
// background AICF triage batches, each of which costs a miner GPU time.
let crawlTriageTimer = null;

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
    // NOT a product. Every human-facing / indexer-facing surface built from
    // this registry (landing page, OpenAPI) filters devOnly out, and
    // X402_ENV=production disables it outright — the spec is explicit that
    // no surface may advertise the smoke-test echo as something to buy.
    devOnly: true,
    mode: 'execute-then-settle',
    mimeType: 'application/json',
    maxBodyBytes: 4 * 1024,
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


/**
 * Schedule background sweeps.
 *
 * Deliberately a slow drip rather than a crawl: the first sweep waits for the
 * index to exist, each one is bounded by wall clock and probe count, and the
 * timer is unref'd so a harvest can never hold the process open at shutdown.
 */
function startMeshSweeps({ cfg, meshIndex, harvester, logger }) {
  let timer = null;
  let stopped = false;
  const tick = async () => {
    if (stopped) return;
    try {
      const index = await meshIndex.getIndex();
      await harvester.sweep(index.records);
    } catch (e) {
      logger && logger.warn && logger.warn('mesh_sweep_failed', { error: e.message });
    }
  };
  timer = setInterval(() => { tick(); }, Number(cfg.meshSweepIntervalMs));
  if (timer.unref) timer.unref();
  // First sweep shortly after boot, once the index warm-up has had a head start.
  const first = setTimeout(() => { tick(); }, Math.min(Number(cfg.meshSweepIntervalMs), 180_000));
  if (first.unref) first.unref();
  return {
    stop() { stopped = true; harvester.stop(); clearInterval(timer); clearTimeout(first); },
    sweepNow: tick,
  };
}

function createRegistry({ cfg, node, capacity, chainIndex, gatewayStore, inferenceFetch, sleep, now = Date.now, availabilityTtlMs = 5000, logger = null }) {
  let meshHarvestHandle = null;
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
    createTierStandardsProduct({ cfg, capacity, fetchImpl: inferenceFetch }),
  ];

  // 0. Prepaid credits. Listed FIRST because it is the product that makes the
  // cheap ones viable: one settlement, then N gas-free calls (see credits.js
  // for the measured gas floor this exists to defeat). Needs the gateway DB
  // for the voucher balances, same as the commit-reveal product.
  if (gatewayStore) {
    const creditsProduct = createCreditsProduct({ cfg, gatewayStore, now });
    creditsProduct.freeRoutes = [createCreditsBalanceRoute({ cfg, gatewayStore })];
    products.push(creditsProduct);
  }

  // Agent commodities. These are the products agents already buy elsewhere;
  // they exist here because the gateway can serve them from infrastructure
  // this box already runs.
  const fetchProduct = createFetchProduct({ cfg, fetchImpl: inferenceFetch, now });
  // The FREE crawler rides on the fetch product's free-route list: it is the
  // unpaid demonstration of exactly what that product sells, and it reuses the
  // same SSRF-hardened fetch rather than growing a second one.
  if (gatewayStore) {
    fetchProduct.freeRoutes = [
      ...(fetchProduct.freeRoutes || []),
      createFreeCrawlRoute({ cfg, gatewayStore, fetchImpl: inferenceFetch, now }),
    ];
  }
  products.push(fetchProduct);

  // Chain-native products: things only an operator who runs the L1 can sell.
  // Both write to the data-availability layer, and both expose a FREE read
  // route — a proof or an artifact nobody can fetch back is worth nothing.
  const notarize = createNotarizeProduct({ cfg, node, now });
  notarize.freeRoutes = [createNotarizeVerifyRoute({ cfg, node })];
  products.push(notarize);

  const blob = createBlobProduct({ cfg, node, now });
  blob.freeRoutes = [createBlobGetRoute({ cfg, node })];
  products.push(blob);

  // Batch embeddings off the model the deploy indexer already keeps resident.
  products.push(createEmbeddingsProduct({ cfg, fetchImpl: inferenceFetch, now }));
  products.push(createAskUrlProduct({ cfg, fetchImpl: inferenceFetch, now }));

  // Data feeds. The oracle shares ONE price instance with the ANM payment
  // lane, so the rate it attests to is the same rate we price payments at —
  // two instances could drift and quote a buyer something we do not honour.
  const anmPrice = createAnmPrice({ path: cfg.anmPricePath, maxAgeSeconds: cfg.anmPriceMaxAgeSeconds, now });
  products.push(createOracleProduct({ cfg, anmPrice, node, now }));
  products.push(createSnapshotProduct({ cfg, node, now }));
  products.push(createMempoolProduct({ cfg, node, now }));

  // Post-quantum verification — the same verifier the node admits txs with.
  products.push(createPqVerifyProduct({ cfg, now }));

  // GEO audit: fetches a third-party origin, so it shares the fetch product's
  // SSRF guards and gets its own byte/time/concurrency budget.
  if (cfg.geoAuditEnabled) {
    products.push(createGeoAuditProduct({ cfg, fetchImpl: inferenceFetch, now }));
  }
  if (cfg.geoFixEnabled) {
    products.push(createGeoFixProduct({ cfg, fetchImpl: inferenceFetch, now }));
  }
  if (cfg.meshEnabled) {
    // One index cache shared by search and the harvester, so a sweep and a
    // search never build two copies of the same 31k-record index.
    const meshIndex = createMeshIndexCache({ cfg, fetchImpl: inferenceFetch, now, logger, gatewayStore });
    const mesh = createMeshFindProduct({ cfg, fetchImpl: inferenceFetch, now, logger, indexCache: meshIndex, gatewayStore });
    // Build the index in the background at boot rather than on the first paid
    // call: a cold harvest of two directories takes over a minute.
    if (cfg.meshBackgroundEnabled && typeof mesh.warmIndex === 'function') mesh.warmIndex();
    products.push(mesh);

    if (cfg.meshHarvestEnabled && gatewayStore) {
      const harvester = createHarvester({ cfg, gatewayStore, fetchImpl: inferenceFetch, now, logger });
      products.push(createMeshProbeProduct({ cfg, harvester, gatewayStore, now }));
      if (cfg.meshBackgroundEnabled) meshHarvestHandle = startMeshSweeps({ cfg, meshIndex, harvester, logger });
    }

    // Solve plans over the same index, so it sees probe-verified prices too.
    if (cfg.solveEnabled) {
      products.push(createSolveProduct({ cfg, indexCache: meshIndex, fetchImpl: inferenceFetch, now, logger }));
    }

    // x402 ANALYTICS — statistics over the SAME index, so a distribution and a
    // search can never disagree about what the market contains. The narrative
    // layer runs on AICF (the on-chain inference fabric), not on the pool /v1
    // that every product above uses, and reports per call which of the two
    // actually served it.
    if (cfg.analyticsEnabled) {
      products.push(...createAnalyticsProducts({ cfg, indexCache: meshIndex, gatewayStore, fetchImpl: inferenceFetch, now, logger }));
    }
  }

  // PAID CRAWL. Website owners charge AI crawlers for access; the operator
  // side is entirely free (register, decide, verify, earnings) and the only
  // paid surface is the crawler buying a pass. Needs the gateway DB for the
  // site registry, the grace counters and the passes.
  if (gatewayStore && cfg.crawlEnabled) {
    const crawlGate = createCrawlGate({ cfg, gatewayStore, fetchImpl: inferenceFetch, now, logger });
    const passes = crawlGate.passProducts;
    // Every free route hangs off the first pass product: the registry only
    // collects freeRoutes from products, and these routes belong to the same
    // product family even though nobody pays for them.
    passes[0].freeRoutes = [...(passes[0].freeRoutes || []), ...crawlGate.freeRoutes];
    // Post-quantum crawl licences: portable, independently verifiable proof of
    // what a crawler licensed. Free to issue AND free to verify — a receipt
    // nobody can check without paying us is not evidence.
    const crawlLicence = createCrawlLicence({ cfg, gatewayStore, now, logger });
    passes[0].freeRoutes = [...passes[0].freeRoutes, ...crawlLicence.freeRoutes];
    products.push(...passes);

    // Unknown-UA triage on Animica's own inference network. Advisory only —
    // it proposes taxonomy rows for an operator to review and can never
    // classify anyone into a charge. See products/crawl-triage.js.
    if (cfg.crawlTriageEnabled && !crawlTriageTimer) {
      const triage = createCrawlTriage({
        gatewayStore,
        aicf: createAicfEngine({ cfg, fetchImpl: inferenceFetch, now }),
        cfg,
        logger,
        now,
      });
      crawlTriageTimer = triage.schedule({
        intervalMs: cfg.crawlTriageIntervalMs,
        limit: cfg.crawlTriageBatch,
      });
    }
  }

  // Notarised forecasts. Needs the gateway DB so forecasts can be SCORED once
  // their market settles — a forecast product that never grades itself is just
  // an opinion generator.
  if (gatewayStore) {
    const fc = createForecastProduct({ cfg, node, gatewayStore, fetchImpl: inferenceFetch, now });
    fc.freeRoutes = [
      createCalibrationRoute({ cfg, gatewayStore, fetchImpl: inferenceFetch, now }),
      createForecastRecordRoute({ cfg, node, gatewayStore }),
    ];
    products.push(fc);

    // ANIMICA EXECUTE — the flagship. Listed last because it composes the
    // capabilities above rather than adding a new backend.
    products.push(createExecuteProduct({ cfg, node, gatewayStore, fetchImpl: inferenceFetch, now }));

    // The agent utility family. Seven endpoints over one engine whose whole
    // job is that the output shape is validated in code before it ships.
    products.push(...createUtilityProducts({ cfg, fetchImpl: inferenceFetch, now }));
    if (cfg.forecastEnabled && forecastSweepTimer === null) {
      forecastSweepTimer = setInterval(() => {
        resolveOpenForecasts({ gatewayStore, fetchImpl: inferenceFetch, cfg, now }).catch(() => {});
      }, cfg.forecastResolveIntervalMs);
      if (forecastSweepTimer.unref) forecastSweepTimer.unref();
    }
  }

  // Block-reward share leases. DISABLED by default (see lease.js). Needs the
  // gateway DB, because the oversubscription ceiling is enforced by a
  // transaction there rather than by hope.
  if (gatewayStore) {
    products.push(createLeaseProduct({ cfg, node, gatewayStore, anmPrice, now }));
  }

  // A. The randomness family (int / shuffle / pick / bulk / commit-reveal):
  // all five derive from the SAME single verified draw path and share the
  // readiness probe above, so an unhealthy entropy source fails the whole
  // family closed BEFORE any payment is requested. The commit product needs
  // the gateway DB for its sealed secrets and its free public reveal route.
  if (gatewayStore) {
    products.push(...createRandomProducts({ cfg, node, source: randomnessSource, gatewayStore, now }));
  }

  // B. Paid media rendering (image / video / audio). Three products over one
  // shared capability probe, so a catalog scrape costs the marketplace ONE
  // request rather than one per family. Each gates on renderers online for the
  // requested KIND — a box that renders images may advertise no video kind.
  const media = createMediaProducts({ cfg, fetchImpl: inferenceFetch, now });
  products.push(...media.products);
  // Warm the probe once at startup so the first catalog scrape is not a miss;
  // safeProbe never throws, so a marketplace blip cannot break boot.
  media.capacity.safeProbe();
  if (mediaProbeTimer === null && cfg.mediaEnabled) {
    mediaProbeTimer = setInterval(() => { media.capacity.safeProbe(); }, cfg.mediaProbeIntervalMs);
    if (mediaProbeTimer.unref) mediaProbeTimer.unref();
  }

  for (const p of products) {
    p.priceAtomic = cfgMod.usdToUsdcAtomic(p.priceUsd);
    p.cachedAvailability = memoAsync(() => p.availability(), availabilityTtlMs, now);
  }

  // C. Free trials. Attached AFTER cachedAvailability exists, because a trial
  // reuses the product's own availability gate — it must never hand out a
  // sample of a service that would refuse a payer.
  //
  // Caps are per client per UTC day, chosen by what the product costs US to
  // serve. The devOnly echo is excluded (it is not a product), and the
  // commit-reveal product is excluded because its free half already IS the
  // reveal route — a second free route would just be confusing.
  if (cfg.trialsEnabled && gatewayStore) {
    const TRIAL_LIMITS = {
      // classify is the highest-traffic paid text product and the only one
      // that has ever converted an external buyer — who paid blind, after two
      // 402s, with no way to see the output first. Everything else in this
      // family is still trial-less; see the note below.
      classify: cfg.trialLimitCheap,
      // The rest of the same text family, same engine and same price. An agent
      // that cannot sample the output usually skips rather than paying blind,
      // so every one of these gets the same 5-a-day sample as classify.
      extract_structured: cfg.trialLimitCheap,
      entities: cfg.trialLimitCheap,
      json_repair: cfg.trialLimitCheap,
      injection_scan: cfg.trialLimitCheap,
      rerank: cfg.trialLimitCheap,
      route_action: cfg.trialLimitCheap,
      qrng: cfg.trialLimitRandom,
      random_int: cfg.trialLimitRandom,
      random_shuffle: cfg.trialLimitRandom,
      random_pick: cfg.trialLimitRandom,
      random_bulk: cfg.trialLimitRandom,
      bulk_chain: cfg.trialLimitCheap,
      chain_address_history: cfg.trialLimitCheap,
      chain_batch_balances: cfg.trialLimitCheap,
      priority_inference: cfg.trialLimitInference,
      tier_standards: cfg.trialLimitInference,
      media_image: cfg.trialLimitMedia,
      media_video: cfg.trialLimitMedia,
      media_audio: cfg.trialLimitMedia,
      // One free audit a day per client. The report is the sales pitch: a
      // site owner who sees a real 429 against GPTBot does not need copy.
      geo_audit: cfg.trialLimitGeoAudit,
      geo_fix: cfg.trialLimitGeoFix,
      mesh_find: cfg.trialLimitMeshFind,
      solve_plan: cfg.trialLimitSolve,
      analytics_market: cfg.trialLimitAnalytics,
      analytics_price: cfg.trialLimitAnalytics,
      analytics_peers: cfg.trialLimitAnalytics,
    };
    for (const p of products) {
      if (p.devOnly || !p.enabled) continue;
      const limit = TRIAL_LIMITS[p.id];
      if (!limit) continue;
      const route = createTrialRoute({ product: p, cfg, gatewayStore, limitPerDay: limit, now });
      if (!route) continue;
      p.freeRoutes = [...(p.freeRoutes || []), route];
      p.trialLimitPerDay = limit;   // surfaced in the catalog and in every 402
    }
  }

  // Outbound buying. Constructed with the facilitator's ADDRESS purely so the
  // payer can refuse to be it — deriving it here keeps config free of crypto
  // and makes the separation-of-duties rule explicit at the call site.
  if (cfg.execEnabled && cfg.execPrivateKey) {
    let guardAddress = null;
    try {
      const evm = require('../facilitator-evm/evm.js');
      // Read the facilitator key straight from the environment rather than
      // through loadEvmFacilitatorConfig(): that loader fail-closes on ANY
      // invalid facilitator setting, so an unrelated misconfiguration would
      // leave this guard underivable. A safety check must not be switchable off
      // by a problem it has nothing to do with.
      const src = cfg.envSource || process.env;
      const fk = String(src.X402_FACILITATOR_PRIVATE_KEY || '').trim().replace(/^0x/, '');
      if (/^[0-9a-fA-F]{64}$/.test(fk)) guardAddress = evm.privateKeyToAddress(Buffer.from(fk, 'hex'));
    } catch (e) {
      // Failing to derive the guard address must DISABLE buying, never silently
      // permit it: an unenforced separation rule is worse than none, because it
      // reads as protection.
      logger && logger.warn && logger.warn('exec_guard_address_underivable', { error: e.message });
    }
    if (!guardAddress) {
      logger && logger.warn && logger.warn('exec_disabled_no_guard_address', {
        detail: 'could not derive the facilitator address, so the spender-is-facilitator check cannot run',
      });
    } else {
      products.push(createBuyProduct({
        cfg: Object.assign({}, cfg, { facilitatorSpendGuardAddress: guardAddress }),
        gatewayStore, fetchImpl: inferenceFetch, now, logger,
      }));
    }
  }

  const routeIndex = new Map(); // 'METHOD path' -> {product, route}
  for (const p of products) {
    if (!p.enabled) continue;
    for (const r of p.routes) {
      routeIndex.set(`${r.method} ${r.path}`, { product: p, route: r });
    }
  }

  function find(method, pathname) {
    const hit = (
      routeIndex.get(`${method} ${pathname}`) ||
      // nginx may strip the /x402 prefix when proxying — accept both forms.
      routeIndex.get(`${method} /x402${pathname}`) ||
      null
    );
    if (hit || method !== 'GET') return hit;
    // Discovery probe: x402 indexers and first-contact agents GET a resource
    // to read its 402 offer. A POST-only product answering 404 to that probe
    // is invisible to the whole discovery ecosystem — it never learns the
    // price, asset or input schema. Resolve a GET onto the POST route so the
    // paywall can advertise; delivery still requires the real method, because
    // an unpaid GET can only ever reach the 402 branch.
    const post = routeIndex.get(`POST ${pathname}`) || routeIndex.get(`POST /x402${pathname}`);
    return post ? Object.assign({}, post, { probeOnly: true }) : null;
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

  /**
   * Does a free route exist at this path under ANY method? Used to answer a
   * wrong-method probe with 405 + Allow instead of 404. A crawler that GETs a
   * POST-only trial route must learn the endpoint EXISTS — a 404 tells it the
   * product is missing, which is how free trials end up invisible to exactly
   * the agents they were built to attract.
   */
  function findFreeAnyMethod(pathname) {
    for (const entry of freeRoutes) {
      if (entry.route.match(pathname) || entry.route.match(`/x402${pathname}`)) return entry;
    }
    return null;
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
   * {name, provider, homepage, gateway, network, chain_id, asset,
   * payment_protocol, products: [{id, name, description, method, url, price,
   * currency, available}]} per the discovery spec §1, plus the
   * schema/endpoint/entropy extras indexers and buyers use.
   *
   * Everything here is GENERATED: prices come from the product objects (one
   * source), availability from each product's live hook, network/asset from
   * the same config that builds the accepts array of every 402. There is no
   * second copy of any of it to drift.
   */
  async function catalog() {
    const l = links(cfg);
    const out = Object.assign(
      { x402Version: 2 },
      identity(cfg),
      networkFacts(cfg),
      {
        products: [],
        // Standard x402 discovery list — see the item builder below.
        items: [],
        updated_at: new Date(now()).toISOString(),
      }
    );
    for (const p of products) {
      if (!p.enabled && !p.listedEvenWhenUnavailable) continue;
      let avail;
      try {
        avail = p.enabled ? await p.cachedAvailability() : { available: false, reason: 'disabled' };
      } catch (e) {
        avail = { available: false, reason: 'availability_check_failed', detail: e.message };
      }
      const primary = p.routes[0] || { method: 'GET', path: p.path };
      const entry = {
        id: p.id,
        name: p.title || p.id,
        path: p.path,
        method: primary.method,
        url: l.urlFor(p.path),
        documentation: l.docFor(p.id),
        price: p.priceUsd,
        price_atomic: p.priceAtomic,
        currency: 'USDC',
        description: p.description,
        available: Boolean(avail.available),
        endpoints: p.routes.map((r) => `${r.method} ${r.path}`),
        mimeType: p.mimeType || 'application/json',
      };
      // Development-only surfaces (the settlement smoke echo) say so in the
      // machine catalog too, so nothing downstream can mistake one for a
      // product on offer.
      if (p.devOnly) entry.development_only = true;
      if (!avail.available && avail.reason) entry.unavailable_reason = avail.reason;
      // The reason is the machine code an agent branches on; the detail is
      // the human sentence (e.g. how many workers are serving vs required),
      // so a caller can decide whether to retry without paying to find out.
      if (!avail.available && avail.detail) entry.unavailable_detail = avail.detail;
      // Pre-purchase honesty: the randomness products publish the entropy
      // source the gateway's own readiness draw last observed. The catalog is
      // free, so nobody has to pay to discover that the source is a software
      // CSPRNG with attested:false. availability() ran just above, which is
      // what populates it.
      if (typeof p.entropyDisclosure === 'function') {
        try {
          entry.entropy = p.entropyDisclosure();
        } catch (e) {
          entry.entropy = { source: null, note: `entropy disclosure unavailable: ${e.message}` };
        }
      }
      if (p.trialLimitPerDay) {
        // Hoisted to a first-class field rather than left inside
        // free_endpoints: a discovery indexer scoring "can I evaluate this
        // before paying?" should not have to parse prose to find out.
        entry.free_trial = {
          endpoint: `${p.routes[0].method} ${p.path}/trial`,
          url: l.urlFor(`${p.path}/trial`),
          price: '0',
          limit_per_day: p.trialLimitPerDay,
        };
      }
      if (Array.isArray(p.freeRoutes) && p.freeRoutes.length) {
        // Advertised so an indexer (and a buyer) can see that part of this
        // product costs nothing — e.g. the commit-reveal disclosure.
        entry.free_endpoints = p.freeRoutes.map((r) => ({
          endpoint: `${r.method} ${r.path}`,
          url: l.urlFor(r.path),
          price: '0',
          description: r.description || '',
        }));
      }
      if (p.outputSchema) {
        entry.outputSchema = p.outputSchema; // v1/Bazaar-style {input, output}
      }
      // The PAYMENT TERMS, inline. Without these a discovery crawler has a
      // list of names and prices but nothing it can actually pay, which is
      // why validators reported "0 valid resources" against this catalog
      // despite 24 live products. Built by the SAME buildAccepts that builds
      // real 402s, so the advertised terms can never disagree with what we
      // charge — the price is not copied here, it is generated.
      let accepts = [];
      try {
        accepts = toDiscoveryAccepts(
          buildAccepts({ path: p.path, priceUsd: p.priceUsd }, cfg),
          {
            resource: l.urlFor(p.path),
            description: p.description,
            mimeType: p.mimeType,
            outputSchema: p.outputSchema,
          }
        );
      } catch (e) {
        accepts = [];
      }
      entry.accepts = accepts;
      out.products.push(entry);
      // Standard x402 discovery item. `items[]` with `resource` + `accepts` is
      // the shape indexers and validators actually parse; `products[]` above is
      // this gateway's richer, non-standard view. Both are generated from one
      // registry, so they cannot drift apart.
      out.items.push({
        resource: l.urlFor(p.path),
        type: 'http',
        x402Version: 2,
        accepts,
        lastUpdated: Math.floor(now() / 1000),
        metadata: {
          id: p.id,
          name: p.title || p.id,
          description: p.description,
          method: primary.method,
          mimeType: p.mimeType || 'application/json',
          available: Boolean(avail.available),
          documentation: l.docFor(p.id),
          free_trial: p.trialLimitPerDay ? l.urlFor(`${p.path}/trial`) : undefined,
        },
      });
    }
    return out;
  }

  return {
    products, find, findFree, findFreeAnyMethod, freeRoutes, catalog,
    // Exposed so a shutdown (or a test) can stop background probing rather
    // than relying on unref'd timers to be harmless.
    stopBackground() { if (meshHarvestHandle) meshHarvestHandle.stop(); },
  };
}

module.exports = { createRegistry, memoAsync };
