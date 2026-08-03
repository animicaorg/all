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
  const doc = {
    height: 63080,
    chainId: 1,
    thetaMicro: 25617353,
    networkHashrate: 2.32e9,
    blockTime: 60,
    totalSupply: "105349741.31",
    someFutureField: { nested: true },
  };
  const fetchStub = makeFetchStub(() => jsonResponse(doc));
  const stats = await fetchStats({ fetch: fetchStub });
  assert.equal(fetchStub.calls[0].url, "https://animica.org/api/stats");
  assert.equal(DEFAULT_STATS_URL, "https://animica.org/api/stats");
  assert.equal(stats.height, 63080);
  assert.equal(stats.thetaMicro, 25617353);
  assert.deepEqual(stats.someFutureField, { nested: true });
});

test("fetchStats non-2xx -> HttpError", async () => {
  const fetchStub = makeFetchStub(() => jsonResponse("not found", 404));
  await assert.rejects(
    () => fetchStats({ fetch: fetchStub }),
    (err) => err instanceof HttpError && err.status === 404
  );
});
