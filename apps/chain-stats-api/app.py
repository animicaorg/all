"""Animica chain-stats + supply API.

Aggregator-facing (CoinGecko / CoinMarketCap / WhatToMine / MiningPoolStats)
public read-only HTTP service. Binds 127.0.0.1:8560 behind nginx.

Run:  /root/animica/.venv/bin/uvicorn app:app --host 127.0.0.1 --port 8560

Guarantees:
* ``Access-Control-Allow-Origin: *`` on every response (aggregator pollers).
* Supply endpoints default to text/plain full-precision decimal ANM
  (never scientific notation, never commas) — the format CG/CMC require.
* If the node RPC is down, the last-good snapshot is served with
  ``X-Animica-Stale: true``; the service never returns 500 while a
  last-good snapshot exists.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Dict, Optional, Tuple

from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse, PlainTextResponse

import cache
import sources

app = FastAPI(
    title="Animica Chain Stats API",
    description="Public chain stats + supply endpoints for market/mining aggregators.",
    version="1.0.0",
    docs_url=None,
    redoc_url=None,
)

_AGG_KEY = "aggregate"
_STALE_TTL_S = 10.0  # brief cache for the stale path so a dead node isn't hammered


# ---------------------------------------------------------------------------
# CORS on EVERY response (aggregators poll without an Origin header, so
# starlette's CORSMiddleware — which requires Origin — is not enough).
# OPTIONS (browser preflight) is short-circuited to 204 + CORS headers here,
# before routing, so it can never 405.
# ---------------------------------------------------------------------------

@app.middleware("http")
async def cors_everything(request: Request, call_next):
    if request.method == "OPTIONS":
        response = Response(status_code=204)
    else:
        try:
            response = await call_next(request)
        except Exception:  # belt-and-braces: even unexpected errors return JSON
            response = JSONResponse({"error": "internal error"}, status_code=503)
    response.headers["Access-Control-Allow-Origin"] = "*"
    response.headers["Access-Control-Allow-Methods"] = "GET, HEAD, OPTIONS"
    response.headers["Access-Control-Allow-Headers"] = "*"
    return response


def GET(path: str):
    """GET route that also answers HEAD (FastAPI's @app.get registers GET only,
    which made every HEAD request 405). The ASGI server suppresses the body on
    HEAD responses per HTTP semantics."""
    return app.api_route(path, methods=["GET", "HEAD"])


# ---------------------------------------------------------------------------
# Aggregate access (TTL cache -> live gather -> last-good snapshot)
# ---------------------------------------------------------------------------

def get_aggregate() -> Tuple[Dict[str, Any], bool]:
    """Return (data, stale). Raises only when the node is down AND no
    last-good snapshot exists."""
    hit = cache.CACHE.get(_AGG_KEY)
    if hit is not None:
        return hit["data"], hit["stale"]
    try:
        data = sources.gather()
    except Exception:
        snap = cache.load_snapshot()
        if snap is None:
            raise
        cache.CACHE.set(_AGG_KEY, {"data": snap, "stale": True}, ttl=_STALE_TTL_S)
        return snap, True
    cache.CACHE.set(_AGG_KEY, {"data": data, "stale": False}, ttl=cache.default_ttl())
    cache.save_snapshot(data)
    return data, False


def _try_aggregate() -> Tuple[Optional[Dict[str, Any]], bool, Optional[Exception]]:
    try:
        data, stale = get_aggregate()
        return data, stale, None
    except Exception as exc:
        return None, True, exc


def _stale_headers(stale: bool) -> Dict[str, str]:
    return {"X-Animica-Stale": "true"} if stale else {}


def _unavailable(exc: Optional[Exception]) -> JSONResponse:
    return JSONResponse(
        {
            "error": "upstream node unreachable and no last-good snapshot available",
            "detail": str(exc) if exc else None,
        },
        status_code=503,
    )


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# ---------------------------------------------------------------------------
# Supply endpoints (CG/CMC-grade)
# ---------------------------------------------------------------------------

def _supply_base_units(kind: str, data: Optional[Dict[str, Any]]) -> Optional[int]:
    if kind == "max":
        return sources.MAX_MONEY_BASE_UNITS  # constant, works even with no data
    if data is None:
        return None
    return data["supply"][f"{kind}_base_units"]


def _supply_response(kind: str, request: Request):
    data, stale, exc = _try_aggregate()
    bu = _supply_base_units(kind, data)
    if bu is None:
        return _unavailable(exc)
    if data is None:  # only possible for kind == "max"
        stale = False
    value_str = sources.base_units_to_anm_str(bu)
    headers = _stale_headers(stale)
    if request.query_params.get("format") == "json":
        # CoinGecko requires "value" to be a JSON NUMBER. value_str is already
        # a plain full-precision decimal, i.e. a valid JSON number literal, so
        # the body is built manually — no float round-trip anywhere, the exact
        # digits go on the wire. value_str is kept alongside for safety.
        body = (
            "{"
            f'"value": {value_str}, '
            f'"value_str": {json.dumps(value_str)}, '
            f'"value_base_units": {json.dumps(str(bu))}, '
            '"unit": "ANM", '
            f'"height": {json.dumps(data["height"] if data else None)}, '
            f'"updated_at": {json.dumps(data["updated_at"] if data else _now_iso())}'
            "}"
        )
        return Response(content=body, media_type="application/json", headers=headers)
    # DEFAULT: text/plain decimal in whole-ANM units — what CG/CMC consume.
    return PlainTextResponse(value_str, headers=headers)


@GET("/api/supply/circulating")
def supply_circulating(request: Request):
    return _supply_response("circulating", request)


@GET("/api/supply/total")
def supply_total(request: Request):
    return _supply_response("total", request)


@GET("/api/supply/max")
def supply_max(request: Request):
    return _supply_response("max", request)


_METHODOLOGY = (
    "total = node RPC state.getTotalSupply (sum of all account balances in the "
    "state DB at head). circulating = total minus the live on-chain balances of "
    "the non-circulating addresses listed here (Animica Foundation treasury — "
    "the code-committed recipient of 25% of every block subsidy (15% before block 70,000) since height "
    "42,001 — plus the four protocol-reserved system addresses from "
    "chain.getParams). max = 900,000,000 ANM, the code-enforced hard cap "
    "(MAX_MONEY in consensus/rewards.py). Balances are fetched live from the "
    "node on every refresh (30s cache); nothing is estimated. "
    "Units: 1 ANM = 1e9 nANM base units; values are decimal strings with "
    "decimals applied."
)


@GET("/api/supply")
def supply_full():
    data, stale, exc = _try_aggregate()
    if data is None:
        return _unavailable(exc)
    s = data["supply"]
    non_circ = [
        {
            "address": e["address"],
            "label": e["label"],
            "balance": sources.base_units_to_anm_str(e["balance_base_units"]),
            "balance_base_units": str(e["balance_base_units"]),
        }
        for e in s["non_circulating"]
    ]
    return JSONResponse(
        {
            "total": sources.base_units_to_anm_str(s["total_base_units"]),
            "total_base_units": str(s["total_base_units"]),
            "circulating": sources.base_units_to_anm_str(s["circulating_base_units"]),
            "circulating_base_units": str(s["circulating_base_units"]),
            "max": sources.base_units_to_anm_str(s["max_base_units"]),
            "max_base_units": str(s["max_base_units"]),
            "non_circulating": non_circ,
            "height": data["height"],
            "address_count": s.get("address_count"),
            "methodology": _METHODOLOGY,
            "updated_at": data["updated_at"],
        },
        headers=_stale_headers(stale),
    )


# ---------------------------------------------------------------------------
# Emission schedule (static protocol facts + current values when available)
# ---------------------------------------------------------------------------

@GET("/api/emission")
def emission():
    data, stale, _exc = _try_aggregate()
    current = None
    if data is not None:
        r = data["reward"]
        current = {
            "height": data["height"],
            "epoch": sources.epoch_for_height(data["height"]),
            "block_subsidy_anm": sources.base_units_to_anm_float(r["block_subsidy_base_units"]),
            "miner_anm": sources.base_units_to_anm_float(r["miner_base_units"]),
            "foundation_anm": sources.base_units_to_anm_float(r["foundation_base_units"]),
        }
    return JSONResponse(
        {
            "coin": sources.CHAIN_SYMBOL,
            "decimals": sources.DECIMALS,
            "base_unit": "nANM (1 ANM = 1e9 nANM)",
            "initial_block_reward_anm": 300,
            "target_block_time_s": sources.TARGET_BLOCK_TIME_S,
            "halving": {
                "epoch_length_blocks": sources.EPOCH_LENGTH_BLOCKS,
                "approx_years_per_epoch": 2.57,
                "decay_pct_per_epoch": sources.DECAY_PCT_PER_EPOCH,
                "max_halvings": sources.MAX_HALVINGS,
                "tail_emission_anm_per_block": 0.0001,
                "subsidy_formula": (
                    "subsidy(height) = floor(300e9 * 50^e / 100^e) nANM with "
                    "e = (height-1) // 1,350,000 (exact integer math), floored at "
                    "the 100,000 nANM (0.0001 ANM) tail"
                ),
            },
            "premine_anm": 81000000,
            "block_reward_splits": [
                # A TABLE OF RANGES, not one entry. It previously reported a single
                # hardcoded 85/15 forever, so it was already wrong from block 70,000.
                # Every row is a redistribution of the SAME subsidy total — never new
                # issuance, and the halving schedule is untouched throughout.
                {"from_height": 1, "to_height": 42000, "miner_pct": 100},
                {
                    "from_height": sources.FOUNDATION_SPLIT_HEIGHT,
                    "to_height": sources.TREASURY_25_HEIGHT - 1,
                    "miner_pct": 100 - sources.FOUNDATION_SPLIT_PCT,
                    "foundation_pct": sources.FOUNDATION_SPLIT_PCT,
                    "foundation_address": sources.FOUNDATION_TREASURY_ADDRESS,
                    "note": "FORK_FOUNDATION_SPLIT (7.1.0): 85/15",
                },
                {
                    "from_height": sources.TREASURY_25_HEIGHT,
                    "to_height": sources.SERVICE_CARVE_HEIGHT - 1,
                    "miner_pct": 100 - sources.FOUNDATION_SPLIT_PCT_V2,
                    "foundation_pct": sources.FOUNDATION_SPLIT_PCT_V2,
                    "foundation_address": sources.FOUNDATION_TREASURY_ADDRESS,
                    "note": "FORK_TREASURY_25 (9.4.0): 75/25",
                },
                {
                    "from_height": sources.SERVICE_CARVE_HEIGHT,
                    "miner_pct": (100 - sources.FOUNDATION_SPLIT_PCT_V2
                                  - sources.SERVICE_CARVE_PCT),
                    "foundation_pct": sources.FOUNDATION_SPLIT_PCT_V2,
                    "service_pct": sources.SERVICE_CARVE_PCT,
                    "foundation_address": sources.FOUNDATION_TREASURY_ADDRESS,
                    "note": ("FORK_SERVICE_CARVE (9.5.0): 50 miner / 25 treasury / 25 "
                             "inference. The inference slice is paid out in full every "
                             "block; whatever providers do not claim goes to the treasury"),
                },
            ],
            "iou_settlement": {
                "active_since_height": sources.IOU_SETTLEMENT_HEIGHT,
                "cap_anm_per_block": 50,
                "cap_pct_of_initial_subsidy": 16.7,
                "note": (
                    "FORK_IOU_SETTLEMENT: on-chain settlement of service IOUs "
                    "(dVPN relays, AI compute, media rendering) carved from the "
                    "miner share of the block reward, capped at 50 ANM/block and "
                    "halving with emission; never minted above the subsidy"
                ),
            },
            "max_supply": {
                "hard_cap_anm": 900000000,
                "enforced_in": "consensus/rewards.py compute_block_reward (remaining = MAX_MONEY - premine - cumulative subsidy; rewards cap to remaining, then 0)",
                "math": {
                    "premine_anm": 81000000,
                    "halving_era_emission_anm": 809999806.8744,
                    "halving_era_detail": "sum over epochs 0..21 of floor(300e9/2^e) nANM x 1,350,000 blocks",
                    "tail_through_epoch_63_anm": 5670,
                    "practical_total_anm": 891005476.87,
                    "note": "the residual ~9.0M ANM to the 900M cap only emits via the 0.0001 ANM/block tail (~171,000 years) — 900,000,000 ANM is the absolute code-enforced cap",
                },
            },
            "current": current,
            "updated_at": data["updated_at"] if data else _now_iso(),
        },
        headers=_stale_headers(stale if data is not None else False),
    )


# ---------------------------------------------------------------------------
# Rich stats
# ---------------------------------------------------------------------------

def _pools_list(data: Dict[str, Any]) -> list:
    pool = data.get("pool") or {}
    miners = pool.get("connected_miners")
    if miners is None:
        miners = pool.get("num_miners")
    return [
        {
            "name": sources.POOL_PUBLIC_NAME,
            "url": sources.POOL_PUBLIC_URL,
            "stratum": sources.POOL_PUBLIC_STRATUM,
            "stratum_solo": sources.POOL_PUBLIC_STRATUM_SOLO,
            "fee_bps": sources.pool_fee_bps(),
            "solo_fee_bps": sources.POOL_DEFAULT_SOLO_FEE_BPS,
            "payout_scheme": pool.get("pool_mode") or "pps",
            "miners": miners,
            "workers": pool.get("num_workers"),
            "blocks_found": pool.get("blocks_found_total"),
            "pool_hashrate_hs": pool.get("pool_hashrate_raw_1h_hs"),
        }
    ]


def _iso_or_none(epoch_ts: Optional[int]) -> Optional[str]:
    if not epoch_ts:
        return None
    return datetime.fromtimestamp(epoch_ts, tz=timezone.utc).isoformat(timespec="seconds")


@GET("/api/stats")
def stats():
    data, stale, exc = _try_aggregate()
    if data is None:
        return _unavailable(exc)
    s = data["supply"]
    hr = data["hashrate"]
    blocks = data["blocks"]
    reward = data["reward"]
    price = data.get("price") or {}
    return JSONResponse(
        {
            "name": sources.CHAIN_NAME,
            "symbol": sources.CHAIN_SYMBOL,
            "chain_id": sources.CHAIN_ID,
            "algorithm": sources.ALGORITHM,
            "height": data["height"],
            "difficulty": data["theta_micro"],
            "difficulty_unit": "thetaMicro (PoIES acceptance threshold Theta in micro-nats)",
            "expected_hashes_per_block": data.get("expected_hashes_per_block"),
            "network_hashrate_hs": hr.get("network_hashrate_hs"),
            "network_hashrate_source": "node RPC chain.getNetworkHashrate: sum(e^(thetaMicro_i/1e6)) over the window / elapsed seconds (theta-derived raw SHA3-256 H/s)",
            "pool_observed_hashrate_hs": hr.get("pool_observed_network_hashrate_hs"),
            "block_time_target_s": blocks["block_time_target_s"],
            "avg_block_time_1h_s": blocks.get("avg_block_time_1h_s"),
            "block_reward": sources.base_units_to_anm_float(reward["block_subsidy_base_units"]),
            "block_reward_breakdown": {
                "miner": sources.base_units_to_anm_float(reward["miner_base_units"]),
                "foundation": sources.base_units_to_anm_float(reward["foundation_base_units"]),
            },
            "last_block_time": _iso_or_none(blocks.get("last_block_timestamp")),
            "price_usd": price.get("last_usd"),
            "price_btc": None,
            "volume_24h_usd": price.get("volume_24h_usd"),
            "volume_24h_anm": price.get("volume_24h_anm"),
            "market_url": price.get("market_url"),
            "pools": _pools_list(data),
            "supply": {
                "total_anm": sources.base_units_to_anm_float(s["total_base_units"]),
                "circulating_anm": sources.base_units_to_anm_float(s["circulating_base_units"]),
                "max_anm": sources.base_units_to_anm_float(s["max_base_units"]),
            },
            "updated_at": data["updated_at"],
        },
        headers=_stale_headers(stale),
    )


# ---------------------------------------------------------------------------
# WhatToMine-shaped view (flat, top-level fields)
# ---------------------------------------------------------------------------

@GET("/api/whattomine")
def whattomine():
    data, stale, exc = _try_aggregate()
    if data is None:
        return _unavailable(exc)
    s = data["supply"]
    blocks = data["blocks"]
    reward = data["reward"]
    price = data.get("price") or {}
    return JSONResponse(
        {
            "name": sources.CHAIN_NAME,
            "tag": sources.CHAIN_SYMBOL,
            "symbol": sources.CHAIN_SYMBOL,
            "algorithm": sources.ALGORITHM,
            "block_time": blocks.get("avg_block_time_1h_s") or blocks["block_time_target_s"],
            "block_time_target": blocks["block_time_target_s"],
            "block_reward": sources.base_units_to_anm_float(reward["miner_base_units"]),
            "block_reward_total": sources.base_units_to_anm_float(reward["block_subsidy_base_units"]),
            "last_block": data["height"],
            # 'difficulty' here = expected hashes per block, e^(thetaMicro/1e6),
            # so nethash ~= difficulty / block_time (see README).
            "difficulty": data.get("expected_hashes_per_block"),
            "theta_micro": data["theta_micro"],
            "nethash": data["hashrate"].get("network_hashrate_hs"),
            "exchange_rate": price.get("last_usd"),
            "exchange_rate_curr": "USDT",
            "exchange_rate_vol": price.get("volume_24h_usd"),
            "market_url": price.get("market_url"),
            "circulating_supply": sources.base_units_to_anm_float(s["circulating_base_units"]),
            "total_supply": sources.base_units_to_anm_float(s["total_base_units"]),
            "max_supply": sources.base_units_to_anm_float(s["max_base_units"]),
            "pool_url": sources.POOL_PUBLIC_URL,
            "stratum": sources.POOL_PUBLIC_STRATUM,
            "timestamp": int(datetime.now(timezone.utc).timestamp()),
        },
        headers=_stale_headers(stale),
    )


# ---------------------------------------------------------------------------
# Iquidus/WhatToMine-style plain-text aliases (bare number, text/plain, same
# cached snapshot + stale semantics as the supply routes)
# ---------------------------------------------------------------------------

def _plain_float(v: float) -> str:
    """Float -> plain decimal text: never scientific notation, no trailing zeros."""
    s = f"{v:.3f}"
    return s.rstrip("0").rstrip(".") if "." in s else s


@GET("/api/getblockcount")
def getblockcount():
    data, stale, exc = _try_aggregate()
    if data is None:
        return _unavailable(exc)
    return PlainTextResponse(str(data["height"]), headers=_stale_headers(stale))


@GET("/api/getdifficulty")
def getdifficulty():
    data, stale, exc = _try_aggregate()
    if data is None:
        return _unavailable(exc)
    return PlainTextResponse(str(data["theta_micro"]), headers=_stale_headers(stale))


@GET("/api/getnetworkhashps")
def getnetworkhashps():
    data, stale, exc = _try_aggregate()
    if data is None:
        return _unavailable(exc)
    hs = data["hashrate"].get("network_hashrate_hs")
    if hs is None:
        # theta-derived fallback (same formula, single-block estimate):
        # e^(thetaMicro/1e6) expected hashes / observed (or target) block time.
        blocks = data["blocks"]
        bt = blocks.get("avg_block_time_1h_s") or blocks["block_time_target_s"]
        exp_hashes = data.get("expected_hashes_per_block")
        if exp_hashes and bt:
            hs = exp_hashes / bt
    if hs is None:
        return _unavailable(exc)
    return PlainTextResponse(_plain_float(float(hs)), headers=_stale_headers(stale))


@GET("/api/getblockreward")
def getblockreward():
    data, stale, exc = _try_aggregate()
    if data is None:
        return _unavailable(exc)
    bu = data["reward"]["block_subsidy_base_units"]
    return PlainTextResponse(sources.base_units_to_anm_str(bu), headers=_stale_headers(stale))


@GET("/api/getmoneysupply")
def getmoneysupply():
    data, stale, exc = _try_aggregate()
    if data is None:
        return _unavailable(exc)
    bu = data["supply"]["total_base_units"]
    return PlainTextResponse(sources.base_units_to_anm_str(bu), headers=_stale_headers(stale))


# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------

@GET("/healthz")
def healthz():
    data, stale, _exc = _try_aggregate()
    node_reachable = data is not None and not stale
    price = sources.read_price()  # cheap local-file read; independent of cache
    price_fresh = bool(price and price.get("fresh"))
    # ok tracks node reachability: stale-but-serving stays HTTP 200, but ok is
    # false whenever the node is down so monitors see the outage.
    return JSONResponse(
        {
            "ok": node_reachable,
            "node_reachable": node_reachable,
            "price_fresh": price_fresh,
            "stale": stale if data is not None else None,
            "height": data["height"] if data else None,
            "updated_at": _now_iso(),
        }
    )
