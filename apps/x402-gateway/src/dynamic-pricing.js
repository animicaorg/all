'use strict';
/*
 * Dynamic x402 pricing pegged to Base settlement gas.
 *
 *   price(product) = min(8, mult[product]) * (settlement_gas_units * gasPriceWei * ethUsd / 1e18)
 *
 * Every paid call is priced at a small multiple (5–8x, and 3x for the standard
 * inference tier) of what it costs to settle one USDC transfer on Base — so the
 * price tracks gas and always clears settlement cost with margin. ETH/USD is read
 * ON-CHAIN from the Base Chainlink feed via the SAME Base RPC (no third-party API).
 * A background loop refreshes it; if gas or ETH/USD can't be fetched, prices are
 * left at their static configured values (never zero, never guessed).
 */

// Per-product multiplier. Hard cap 8x is enforced below. Products not listed
// here keep their static price (free/trial/echo are never touched).
const DYN_MULT = {
  random_int: 5, random_shuffle: 5, random_pick: 5, qrng: 5, random_commit: 5,
  chain_batch_balances: 5, chain_address_history: 6, bulk_chain: 6, random_bulk: 6,
  media_image: 6, media_audio: 7, media_video: 8,
  priority_inference: 8,
  tier_standards: 3,           // the standard inference tier — cheap on purpose
  // Products added 2026-08-18. A product NOT listed here keeps a STATIC price,
  // which is a latent correctness bug rather than a pricing preference: when
  // Base gas rises, a static price silently falls under the economic floor and
  // `checkEconomicFloor` REFUSES to settle — so the catalog would advertise a
  // product as available that fails at settlement time. Every paid product
  // belongs in this table.
  fetch_extract: 5,            // one outbound HTTP request
  embed_batch: 5,              // local model, cheap, amortised across the batch
  pq_verify: 5,                // one short-lived local process
  price_oracle: 5,             // a file read plus a head call
  mempool_feed: 5,             // one batched node read
  holder_snapshot: 6,          // up to 1000 accounts in one call
  notarize: 6,                 // a DA write plus a proof read
  blob_put: 6,                 // a DA write, up to 1 MiB
  ask_url: 8,                  // fetch + embed + a model call: three downstreams
  forecast_notarized: 8,       // market fetch + inference + a DA write
  execute: 8,                  // routing + evidence + 1..N model passes + a PQ signature
  // Utility family: one short, low-token model call each, so the cheapest
  // multiple. These are meant to be bought in volume via credits.
  extract_structured: 5, classify: 5, entities: 5, json_repair: 5,
  injection_scan: 5, rerank: 5, route_action: 5,
  // Eighteen outbound requests against a third-party origin — the most of any
  // product here — but no GPU, so it sits just under the inference-heavy 8s.
  geo_audit: 7,
  // The audit's crawl, plus fetching every link it will publish, plus one
  // model call for the prose.
  geo_fix: 8,
  // Search over a cached index: cheap per call, and meant to be bought
  // before every purchase decision, so it sits at the low multiple.
  mesh_find: 5,
  // One outbound request to a third party, no inference.
  mesh_probe: 5,
  // Decomposition model call plus a search per step, over a cached index.
  solve_plan: 8,
  // Analytics: arithmetic over the cached index plus one AICF narration call.
  // Same multiple as solve — one model call, and bought before a decision
  // rather than in volume.
  analytics_market: 8,
  analytics_price: 8,
  analytics_peers: 8,
};

/**
 * Products that are DELIBERATELY fixed-price, with the reason. These are not
 * per-call prices tracking a per-call cost: they are pack/contract sizes far
 * above any plausible gas floor, so pegging them to gas would be meaningless.
 * Anything not in DYN_MULT and not here is an oversight — see the guard test.
 */
const FIXED_PRICE_BY_DESIGN = {
  credits_buy: 'a prepaid pack size ($0.50), not a per-call price; ~100x the gas floor',
  mining_lease: 'a lease contract priced against the ANM rate, not against Base gas',
  echo: 'development-only smoke test, never sold',
  crawl_pass: 'a prepaid bulk pack ($0.01), not a per-call price; the site sets the per-page rate and the pack size divided by it is the request budget — ~10x the gas floor',
  crawl_pass_10: 'the $0.10 crawl pack; same prepaid-denomination reason as crawl_pass, ~100x the gas floor',
  crawl_pass_100: 'the $1.00 crawl pack; same prepaid-denomination reason as crawl_pass, ~1000x the gas floor',
};
const MULT_CAP = 8;

// Base mainnet Chainlink ETH/USD aggregator (8 decimals). Override via env.
const DEFAULT_ETH_USD_FEED = '0x71041dddaD3595F9CEd3DcCFBe3D1F4b0a16Bb70';
const LATEST_ROUND_DATA = '0xfeaf968c';   // latestRoundData()

async function _rpc(fetchImpl, url, method, params) {
  const r = await fetchImpl(url, {
    method: 'POST',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify({ jsonrpc: '2.0', id: 1, method, params }),
    signal: AbortSignal.timeout(8000),
  });
  const j = await r.json();
  if (j.error) throw new Error(j.error.message || 'rpc error');
  return j.result;
}

async function fetchSettlementGasUsd(cfg, fetchImpl) {
  const url = cfg.rpcUrl || cfg.baseRpcUrl || process.env.X402_RPC_URL;
  if (!url) throw new Error("no Base RPC url (X402_RPC_URL)");
  const gasUnits = Number(process.env.X402_SETTLEMENT_GAS_UNITS || 80000);
  const feed = process.env.X402_ETH_USD_FEED || DEFAULT_ETH_USD_FEED;
  const gasWei = BigInt(await _rpc(fetchImpl, url, 'eth_gasPrice', []));
  const call = await _rpc(fetchImpl, url, 'eth_call', [{ to: feed, data: LATEST_ROUND_DATA }, 'latest']);
  const hex = String(call).replace(/^0x/, '');
  if (hex.length < 128) throw new Error('bad chainlink response');
  const ethUsd = Number(BigInt('0x' + hex.slice(64, 128))) / 1e8;   // answer, 8 decimals
  if (!(ethUsd > 0)) throw new Error('non-positive eth/usd');
  const gasEth = (Number(gasWei) * gasUnits) / 1e18;
  const usd = gasEth * ethUsd;
  if (!(usd > 0) || !isFinite(usd)) throw new Error('bad gas usd');
  return { settlementGasUsd: usd, gasWei, ethUsd, gasUnits };
}

function applyDynamicPrices(products, settlementGasUsd, cfgMod, log) {
  if (!(settlementGasUsd > 0)) return 0;
  let n = 0;
  for (const p of products) {
    if (!(p && p.id in DYN_MULT)) continue;
    const mult = Math.min(MULT_CAP, DYN_MULT[p.id]);
    const priceUsd = (mult * settlementGasUsd).toFixed(6);
    try {
      const atomic = cfgMod.usdToUsdcAtomic(priceUsd);   // throws on invalid
      p.priceUsd = priceUsd;
      p.priceAtomic = atomic;
      n++;
    } catch (e) { /* keep static */ }
  }
  return n;
}

function startDynamicPricing({ products, cfg, cfgMod, fetchImpl, logger, intervalMs }) {
  const fetchI = fetchImpl || fetch;
  const log = logger || { info() {}, warn() {} };
  const iv = intervalMs || Number(process.env.X402_DYNAMIC_PRICE_INTERVAL_MS || 30000);
  async function tick() {
    try {
      const g = await fetchSettlementGasUsd(cfg, fetchI);
      const n = applyDynamicPrices(products, g.settlementGasUsd, cfgMod, log);
      log.info && log.info('x402_dynamic_prices', {
        settlement_gas_usd: Number(g.settlementGasUsd.toFixed(6)),
        eth_usd: Math.round(g.ethUsd), gas_gwei: Number((Number(g.gasWei) / 1e9).toFixed(4)),
        products_priced: n,
      });
    } catch (e) {
      log.warn && log.warn('x402_dynamic_prices_skip', { error: String(e && e.message || e) });
    }
  }
  tick();
  const t = setInterval(tick, iv);
  if (t.unref) t.unref();
  return { stop() { clearInterval(t); } };
}

module.exports = {
  FIXED_PRICE_BY_DESIGN, DYN_MULT, MULT_CAP, fetchSettlementGasUsd, applyDynamicPrices, startDynamicPricing };
