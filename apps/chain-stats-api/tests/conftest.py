"""Offline test fixtures: all httpx traffic is served by an httpx.MockTransport
(no sockets), the price feed is a tmp file, and the last-good snapshot lives in
a tmp dir."""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import httpx
import pytest

APP_DIR = Path(__file__).resolve().parents[1]
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

import cache  # noqa: E402
import sources  # noqa: E402

# --- canned upstream data (mirrors real live readings from 2026-08-03) -------

TOTAL_SUPPLY_HEX = "0x17647d9a02d8abd"      # 105,350,641.310599869 ANM
FOUNDATION_BALANCE_HEX = "0x9347338fd46d"   # 161,934.017025133 ANM
HEAD_HEIGHT = 63080
THETA_MICRO = 25617353
HEAD_TS = 1785784140
HASHRATE_HSPS = 0.5167755013845884          # HashShare/s -> *2^32 = raw H/s

TOTAL_BU = int(TOTAL_SUPPLY_HEX, 16)
FOUNDATION_BU = int(FOUNDATION_BALANCE_HEX, 16)
CIRC_BU = TOTAL_BU - FOUNDATION_BU

TOTAL_STR = "105350641.310599869"
CIRC_STR = "105188707.293574736"
MAX_STR = "900000000"

POOL_STATUS = {
    "host": "pool.animica.org",
    "port": 3333,
    "stratum_url": "stratum+tcp://pool.animica.org:3333",
    "connected_miners": 1,
    "network": "mainnet",
    "synced": True,
}
POOL_SUMMARY = {
    "pool_name": "Animica Stratum Pool",
    "pool_mode": "pps",
    "height": HEAD_HEIGHT,
    "num_miners": 1,
    "num_workers": 1,
    "blocks_found_total": 42988,
    "hashrate_raw_1h": 40563580.0,
    "network_hashrate_hps": 40563580.0,
}


def price_doc(ts: float) -> dict:
    return {
        "symbol": "ANM/USDT",
        "base": "ANM",
        "quote": "USDT",
        "last": 6.716e-05,
        "bid": 6.715e-05,
        "ask": 6.795e-05,
        "change_percent": 3.88,
        "base_volume": 833635.8459,
        "target_volume": 54.854,
        "market_url": "https://nonkyc.io/market/ANM_USDT",
        "source": "nonkyc",
        "ts": ts,
    }


def _rpc_dispatch(method: str, params: dict):
    if method == "chain.getHead":
        return {"height": HEAD_HEIGHT, "thetaMicro": THETA_MICRO, "chainId": 1,
                "canonicalHeight": HEAD_HEIGHT, "hash": "0x00"}
    if method == "state.getTotalSupply":
        return {"height": HEAD_HEIGHT, "totalSupply": TOTAL_SUPPLY_HEX, "addressCount": 128}
    if method == "state.getBalance":
        addr = params.get("address")
        if addr == sources.FOUNDATION_TREASURY_ADDRESS:
            return FOUNDATION_BALANCE_HEX
        return "0x0"
    if method == "chain.getNetworkHashrate":
        return {"hashrate_hsps": HASHRATE_HSPS, "window_blocks": 120,
                "window_seconds": 11853.0, "height_start": 62961,
                "height_end": HEAD_HEIGHT, "method": "theta_micro_expected_trials"}
    if method == "chain.getBlockByHeight":
        h = int(params.get("height"))
        if h == HEAD_HEIGHT:
            return {"number": h, "timestamp": HEAD_TS}
        if h == HEAD_HEIGHT - 60:
            return {"number": h, "timestamp": HEAD_TS - 3600}  # exact 60s avg
        return {"number": h, "timestamp": 0}
    raise AssertionError(f"unexpected RPC method in test: {method}")


@pytest.fixture
def upstream(monkeypatch, tmp_path):
    """Install a MockTransport for every httpx client sources creates, point the
    price file + snapshot path at tmp files, and clear the TTL cache."""
    state = {"rpc_down": False, "pool_down": False}

    price_file = tmp_path / "anm-price.json"
    price_file.write_text(json.dumps(price_doc(time.time())))
    snapshot = tmp_path / "state" / "last_good.json"

    monkeypatch.setenv("ANIMICA_STATS_PRICE_FILE", str(price_file))
    monkeypatch.setenv("ANIMICA_STATS_SNAPSHOT_PATH", str(snapshot))
    monkeypatch.delenv("ANIMICA_NON_CIRCULATING", raising=False)

    def handler(request: httpx.Request) -> httpx.Response:
        url = request.url
        if url.port == 8545:
            if state["rpc_down"]:
                raise httpx.ConnectError("mock: node down", request=request)
            body = json.loads(request.content)
            result = _rpc_dispatch(body["method"], body.get("params") or {})
            return httpx.Response(200, json={"jsonrpc": "2.0", "id": body["id"], "result": result})
        if url.port == 8550:
            if state["pool_down"]:
                raise httpx.ConnectError("mock: pool down", request=request)
            if url.path == "/v1/pool/status":
                return httpx.Response(200, json=POOL_STATUS)
            if url.path == "/api/pool/summary":
                return httpx.Response(200, json=POOL_SUMMARY)
            return httpx.Response(404)
        if url.host == "animica.org":
            return httpx.Response(200, json=price_doc(time.time()))
        return httpx.Response(404)

    def make_client(timeout=None):
        return httpx.Client(transport=httpx.MockTransport(handler))

    monkeypatch.setattr(sources, "_make_client", make_client)

    cache.CACHE.clear()
    state["price_file"] = price_file
    state["snapshot"] = snapshot
    yield state
    cache.CACHE.clear()


@pytest.fixture
def client(upstream):
    from fastapi.testclient import TestClient
    import app as app_module
    with TestClient(app_module.app, raise_server_exceptions=False) as c:
        yield c
