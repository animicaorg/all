"""Real, read-only ecosystem data collectors. Every collector returns Metric objects tagged OK or
UNAVAILABLE — a source that can't be read is reported as unavailable, NEVER guessed or fabricated.
"""

from __future__ import annotations

import json
import time
import urllib.request
import urllib.error
from dataclasses import dataclass, asdict
from typing import Any, List, Optional

from .config import GrowthConfig


@dataclass
class Metric:
    key: str
    value: Any            # None when unavailable
    status: str           # "OK" | "UNAVAILABLE"
    source: str           # url/service the number came from (provenance)
    unit: str = ""
    note: str = ""

    def as_dict(self) -> dict:
        return asdict(self)


def _get_json(url: str, *, timeout: float = 6.0, headers: Optional[dict] = None,
              method: str = "GET", body: Optional[bytes] = None) -> Optional[Any]:
    try:
        req = urllib.request.Request(url, data=body, method=method, headers=headers or {})
        if body is not None and "Content-Type" not in (headers or {}):
            req.add_header("Content-Type", "application/json")
        with urllib.request.urlopen(req, timeout=timeout) as r:  # nosec B310
            return json.loads(r.read().decode())
    except Exception:
        return None


def _rpc(url: str, method: str, params: Optional[list] = None) -> Optional[Any]:
    body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params or []}).encode()
    d = _get_json(url, method="POST", body=body, timeout=6.0)
    if isinstance(d, dict) and "result" in d:
        return d["result"]
    return None


def _num(d: Any, *path, default=None):
    cur = d
    for p in path:
        if isinstance(cur, dict) and p in cur:
            cur = cur[p]
        else:
            return default
    return cur


def collect(cfg: GrowthConfig) -> List[Metric]:
    metrics: List[Metric] = []

    # ---- chain (RPC) ----
    head = _rpc(cfg.rpc_url, "chain.getHead") or _rpc(cfg.rpc_url, "chain.getHeader")
    height = _num(head, "height") if isinstance(head, dict) else (head if isinstance(head, int) else None)
    metrics.append(Metric("chain.height", height, "OK" if height is not None else "UNAVAILABLE", cfg.rpc_url, "block"))
    health = _rpc(cfg.rpc_url, "system.health")
    peers = _num(health, "peers") if isinstance(health, dict) else None
    metrics.append(Metric("chain.peers", peers, "OK" if peers is not None else "UNAVAILABLE", cfg.rpc_url, "peer"))

    # ---- marketplace ----
    mkt_health = _get_json(f"{cfg.mkt_url}/api/mkt/v1/health")
    metrics.append(Metric("mkt.up", bool(mkt_health), "OK" if mkt_health is not None else "UNAVAILABLE", cfg.mkt_url))
    nl = _get_json(f"{cfg.mkt_url}/api/mkt/v1/newsletter/stats")
    if isinstance(nl, dict):
        metrics.append(Metric("newsletter.confirmed", nl.get("confirmed"), "OK", cfg.mkt_url, "subscriber"))
        metrics.append(Metric("newsletter.pending", nl.get("pending"), "OK", cfg.mkt_url, "subscriber"))
    else:
        metrics.append(Metric("newsletter.confirmed", None, "UNAVAILABLE", cfg.mkt_url, "subscriber"))
    listings = _get_json(f"{cfg.mkt_url}/api/mkt/v1/listings?limit=1")
    lcount = _num(listings, "total") if isinstance(listings, dict) else None
    metrics.append(Metric("mkt.listings", lcount, "OK" if lcount is not None else "UNAVAILABLE", cfg.mkt_url, "listing"))

    # ---- free-AI usage ----
    ai = _get_json(cfg.ai_stats_url)
    reqs = _num(ai, "total") if isinstance(ai, dict) else (_num(ai, "requests") if isinstance(ai, dict) else None)
    metrics.append(Metric("ai.requests_served", reqs, "OK" if reqs is not None else "UNAVAILABLE", cfg.ai_stats_url, "request"))

    # ---- NonKYC market (price/volume) ----
    tk = _get_json(cfg.nonkyc_ticker)
    if isinstance(tk, dict):
        last = tk.get("last_price") or tk.get("lastPrice") or tk.get("last")
        vol = tk.get("volume") or tk.get("baseVolume") or tk.get("quoteVolume")
        metrics.append(Metric("market.anm_usdt", last, "OK" if last is not None else "UNAVAILABLE", cfg.nonkyc_market, "USDT"))
        metrics.append(Metric("market.volume", vol, "OK" if vol is not None else "UNAVAILABLE", cfg.nonkyc_market))
    else:
        metrics.append(Metric("market.anm_usdt", None, "UNAVAILABLE", cfg.nonkyc_market, "USDT"))

    # ---- pool ----
    pool = _get_json(f"{cfg.pool_url}/api/stats") or _get_json(f"{cfg.pool_url}/stats")
    hr = _num(pool, "hashrate") if isinstance(pool, dict) else None
    metrics.append(Metric("pool.hashrate", hr, "OK" if hr is not None else "UNAVAILABLE", cfg.pool_url, "H/s"))

    return metrics


def snapshot(cfg: GrowthConfig) -> dict:
    ms = collect(cfg)
    ok = [m for m in ms if m.status == "OK"]
    return {
        "ts": int(time.time()),
        "metrics": [m.as_dict() for m in ms],
        "ok_count": len(ok),
        "unavailable": [m.key for m in ms if m.status != "OK"],
    }
