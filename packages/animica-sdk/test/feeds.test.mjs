import { test } from "node:test";
import assert from "node:assert/strict";
import {
  fetchPrice,
  fetchStats,
  DEFAULT_PRICE_URL,
  DEFAULT_STATS_URL,
  HttpError,
} from "../dist/esm/index.js";
import { makeFetchStub, jsonResponse } from "./helpers.mjs";

// Sample captured live from https://animica.org/anm-price.json (2026-08-03).
const PRICE_DOC = {
  symbol: "ANM/USDT",
  base: "ANM",
  quote: "USDT",
  last: 6.716e-5,
  bid: 6.715e-5,
  ask: 6.795e-5,
  mid: 6.755e-5,
  display: 6.716e-5,
  is_indicative: false,
  change_percent: 3.88,
  base_volume: 833635.8459,
  target_volume: 54.854,
  high: 6.838e-5,
  low: 6.39e-5,
  market_url: "https://nonkyc.io/market/ANM_USDT",
  pool_url: "https://nonkyc.io/pool/ANM_USDT",
  source: "nonkyc",
  ts: 1785784392,
};

test("fetchPrice hits the default URL and returns typed doc", async () => {
  const fetchStub = makeFetchStub(() => jsonResponse(PRICE_DOC));
  const price = await fetchPrice({ fetch: fetchStub });
  assert.equal(fetchStub.calls[0].url, "https://animica.org/anm-price.json");
  assert.equal(DEFAULT_PRICE_URL, "https://animica.org/anm-price.json");
  assert.equal(price.symbol, "ANM/USDT");
  assert.equal(price.last, 6.716e-5);
  assert.equal(price.base_volume, 833635.8459);
  assert.equal(price.is_indicative, false);
});

test("fetchPrice custom url + non-2xx -> HttpError", async () => {
  const fetchStub = makeFetchStub(() => jsonResponse({ nope: 1 }, 502));
  await assert.rejects(
    () => fetchPrice({ url: "http://feed.test/p.json", fetch: fetchStub }),
    (err) => {
      assert.ok(err instanceof HttpError);
      assert.equal(err.status, 502);
      assert.equal(err.url, "http://feed.test/p.json");
      return true;
    }
  );
});

test("fetchStats hits default URL and preserves unknown fields", async () => {
  // Shape captured live from the chain-stats service /api/stats (2026-08-03).
  const doc = {
    name: "Animica",
    symbol: "ANM",
    chain_id: 1,
    algorithm: "SHA3-256 PoW (PoIES)",
    height: 63130,
    difficulty: 25645361,
    difficulty_unit: "thetaMicro (PoIES acceptance threshold Theta in micro-nats)",
    expected_hashes_per_block: 137289956910.727,
    network_hashrate_hs: 1843871486.5668,
    pool_observed_hashrate_hs: 59652323.5555,
    block_time_target_s: 60,
    avg_block_time_1h_s: 78.37,
    block_reward: 300.0,
    block_reward_breakdown: { miner: 255.0, foundation: 45.0 },
    last_block_time: "2026-08-03T20:11:09+00:00",
    price_usd: 6.716e-5,
    price_btc: null,
    volume_24h_usd: 49.2554,
    market_url: "https://nonkyc.io/market/ANM_USDT",
    pools: [
      {
        name: "Animica Official Pool",
        url: "https://pool.animica.org",
        stratum: "stratum+tcp://pool.animica.org:3333",
        fee_bps: 0,
        payout_scheme: "pps",
      },
    ],
    supply: { total_anm: 105365641.32, circulating_anm: 105201457.3, max_anm: 900000000.0 },
    updated_at: "2026-08-03T20:11:51+00:00",
    someFutureField: { nested: true },
  };
  const fetchStub = makeFetchStub(() => jsonResponse(doc));
  const stats = await fetchStats({ fetch: fetchStub });
  assert.equal(fetchStub.calls[0].url, "https://animica.org/api/stats");
  assert.equal(DEFAULT_STATS_URL, "https://animica.org/api/stats");
  assert.equal(stats.height, 63130);
  assert.equal(stats.difficulty, 25645361);
  assert.equal(stats.network_hashrate_hs, 1843871486.5668);
  assert.deepEqual(stats.block_reward_breakdown, { miner: 255.0, foundation: 45.0 });
  assert.equal(stats.price_btc, null);
  assert.equal(stats.pools[0].stratum, "stratum+tcp://pool.animica.org:3333");
  assert.equal(stats.supply.max_anm, 900000000.0);
  assert.deepEqual(stats.someFutureField, { nested: true });
});

test("fetchStats non-2xx -> HttpError", async () => {
  const fetchStub = makeFetchStub(() => jsonResponse("not found", 404));
  await assert.rejects(
    () => fetchStats({ fetch: fetchStub }),
    (err) => err instanceof HttpError && err.status === 404
  );
});
