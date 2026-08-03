"""Endpoint tests against the FastAPI app with all upstreams mocked
(httpx.MockTransport; tmp price file; tmp snapshot). Fully offline."""
from __future__ import annotations

import json
import time

import cache
import sources
from conftest import (
    CIRC_STR,
    HEAD_HEIGHT,
    HASHRATE_HSPS,
    MAX_STR,
    THETA_MICRO,
    TOTAL_STR,
    price_doc,
)

ALL_GET_PATHS = [
    "/api/supply/circulating",
    "/api/supply/total",
    "/api/supply/max",
    "/api/supply",
    "/api/emission",
    "/api/stats",
    "/api/whattomine",
    "/healthz",
]


# --- plain-text supply (CG/CMC format) ---------------------------------------

def test_supply_plaintext_defaults(client):
    r = client.get("/api/supply/circulating")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/plain")
    assert r.text == CIRC_STR
    assert "e" not in r.text.lower() and "," not in r.text
    assert "x-animica-stale" not in r.headers

    assert client.get("/api/supply/total").text == TOTAL_STR
    assert client.get("/api/supply/max").text == MAX_STR


def test_supply_json_format(client):
    r = client.get("/api/supply/circulating?format=json")
    assert r.status_code == 200
    body = r.json()
    assert body["value"] == CIRC_STR
    assert body["value_base_units"] == "105188707293574736"
    assert body["unit"] == "ANM"
    assert body["height"] == HEAD_HEIGHT
    assert body["updated_at"]


def test_supply_full_breakdown(client):
    r = client.get("/api/supply")
    assert r.status_code == 200
    b = r.json()
    assert b["total"] == TOTAL_STR
    assert b["circulating"] == CIRC_STR
    assert b["max"] == MAX_STR
    assert b["height"] == HEAD_HEIGHT
    assert "methodology" in b and "state.getTotalSupply" in b["methodology"]
    entries = {e["address"]: e for e in b["non_circulating"]}
    assert len(entries) == 5  # foundation treasury + 4 params system addresses
    f = entries[sources.FOUNDATION_TREASURY_ADDRESS]
    assert f["balance"] == "161934.017025133"
    assert "Foundation" in f["label"]
    # circulating = total - sum(non-circulating), exactly
    total_bu = int(b["total_base_units"])
    non_circ_sum = sum(int(e["balance_base_units"]) for e in b["non_circulating"])
    assert int(b["circulating_base_units"]) == total_bu - non_circ_sum


# --- CORS on every response --------------------------------------------------

def test_cors_header_on_every_endpoint(client):
    for path in ALL_GET_PATHS:
        r = client.get(path)
        assert r.headers.get("access-control-allow-origin") == "*", path


def test_cors_present_even_on_503(client, upstream):
    upstream["rpc_down"] = True
    cache.CACHE.clear()
    # no snapshot was ever written in this fresh env -> 503, but still CORS
    r = client.get("/api/stats")
    assert r.status_code == 503
    assert r.headers.get("access-control-allow-origin") == "*"


# --- stale serving from last-good snapshot -----------------------------------

def test_stale_served_when_rpc_down(client, upstream):
    fresh = client.get("/api/supply/total")
    assert fresh.status_code == 200 and fresh.text == TOTAL_STR
    assert upstream["snapshot"].exists()  # last-good persisted

    upstream["rpc_down"] = True
    cache.CACHE.clear()

    stale = client.get("/api/supply/total")
    assert stale.status_code == 200
    assert stale.text == TOTAL_STR
    assert stale.headers.get("x-animica-stale") == "true"

    stats = client.get("/api/stats")
    assert stats.status_code == 200
    assert stats.headers.get("x-animica-stale") == "true"
    assert stats.json()["height"] == HEAD_HEIGHT


def test_never_500_without_snapshot(client, upstream):
    upstream["rpc_down"] = True
    cache.CACHE.clear()
    r = client.get("/api/supply/circulating")
    assert r.status_code == 503  # explicit unavailability, not a 500
    # max supply is a code constant: still served
    r2 = client.get("/api/supply/max")
    assert r2.status_code == 200 and r2.text == MAX_STR
    # health never errors
    h = client.get("/healthz")
    assert h.status_code == 200
    assert h.json()["ok"] is True
    assert h.json()["node_reachable"] is False


# --- stats & whattomine shapes -----------------------------------------------

def test_stats_shape_and_values(client):
    r = client.get("/api/stats")
    assert r.status_code == 200
    s = r.json()
    for key in (
        "name", "symbol", "algorithm", "height", "difficulty",
        "network_hashrate_hs", "block_time_target_s", "avg_block_time_1h_s",
        "block_reward", "block_reward_breakdown", "last_block_time",
        "price_usd", "price_btc", "volume_24h_usd", "market_url", "pools",
        "updated_at",
    ):
        assert key in s, key
    assert s["name"] == "Animica" and s["symbol"] == "ANM"
    assert s["height"] == HEAD_HEIGHT
    assert s["difficulty"] == THETA_MICRO
    assert abs(s["network_hashrate_hs"] - HASHRATE_HSPS * 2**32) < 1.0
    assert s["block_time_target_s"] == 60
    assert s["avg_block_time_1h_s"] == 60.0
    assert s["block_reward"] == 300.0
    assert s["block_reward_breakdown"] == {"miner": 255.0, "foundation": 45.0}
    assert s["price_usd"] == 6.716e-05
    assert s["price_btc"] is None
    assert s["last_block_time"].startswith("2026-")
    pool = s["pools"][0]
    assert pool["name"] == "Animica Official Pool"
    assert pool["url"] == "https://pool.animica.org"
    assert pool["stratum"] == "stratum+tcp://pool.animica.org:3333"
    assert pool["fee_bps"] == 0
    assert pool["miners"] == 1


def test_whattomine_shape(client):
    r = client.get("/api/whattomine")
    assert r.status_code == 200
    w = r.json()
    for key in (
        "name", "tag", "algorithm", "block_time", "block_reward", "last_block",
        "difficulty", "nethash", "exchange_rate", "exchange_rate_curr",
        "timestamp",
    ):
        assert key in w, key
    assert w["tag"] == "ANM"
    assert w["last_block"] == HEAD_HEIGHT
    assert w["block_reward"] == 255.0          # miner share (what a miner earns)
    assert w["block_reward_total"] == 300.0
    assert w["difficulty"] > 0                  # expected hashes per block
    assert abs(w["nethash"] - HASHRATE_HSPS * 2**32) < 1.0
    assert w["exchange_rate"] == 6.716e-05
    assert w["block_time"] == 60.0


def test_pool_down_gives_nulls_not_errors(client, upstream):
    upstream["pool_down"] = True
    r = client.get("/api/stats")
    assert r.status_code == 200
    s = r.json()
    assert s["pools"][0]["miners"] is None      # unavailable -> null, not fabricated
    assert s["height"] == HEAD_HEIGHT           # chain data unaffected
    assert "x-animica-stale" not in r.headers


# --- emission & health -------------------------------------------------------

def test_emission_schedule(client):
    r = client.get("/api/emission")
    assert r.status_code == 200
    e = r.json()
    assert e["initial_block_reward_anm"] == 300
    assert e["halving"]["epoch_length_blocks"] == 1_350_000
    assert e["halving"]["decay_pct_per_epoch"] == 50
    assert e["max_supply"]["hard_cap_anm"] == 900_000_000
    splits = e["block_reward_splits"]
    assert splits[-1]["foundation_pct"] == 15
    assert splits[-1]["from_height"] == 42001
    assert e["iou_settlement"]["active_since_height"] == 50000
    assert e["iou_settlement"]["cap_anm_per_block"] == 50
    assert e["current"]["block_subsidy_anm"] == 300.0


def test_healthz_fresh(client):
    h = client.get("/healthz").json()
    assert h["ok"] is True
    assert h["node_reachable"] is True
    assert h["price_fresh"] is True


def test_healthz_price_stale(client, upstream):
    upstream["price_file"].write_text(json.dumps(price_doc(time.time() - 86400)))
    h = client.get("/healthz").json()
    assert h["ok"] is True
    assert h["price_fresh"] is False
