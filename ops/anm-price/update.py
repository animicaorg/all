#!/usr/bin/env python3
"""Fetch the ANM/USDT price from NonKYC and publish a small same-origin
anm-price.json into every Animica web root.

NonKYC's public API sends no CORS header, so browsers can't fetch it directly
and animica.org is a static site with no backend. Instead of a proxy service we
just materialise a tiny JSON file into each served web root here (run every 60s
by anm-price.timer); each site's ticker then fetches its own /anm-price.json
same-origin. If a fetch fails we leave the existing files untouched (last-good),
so a NonKYC hiccup never blanks the widgets.

Pre-trading (last_price == 0) is expected right after listing: we fall back to
the bid/ask mid and flag it indicative so the widget can label it.
"""
from __future__ import annotations

import json
import os
import time
import urllib.request

TICKER_URL = "https://api.nonkyc.io/api/v2/ticker/ANM_USDT"
MARKET_URL = "https://api.nonkyc.io/api/v2/market/getbysymbol/ANM_USDT"
MARKET_LINK = "https://nonkyc.io/market/ANM_USDT"
POOL_LINK = "https://nonkyc.io/pool/ANM_USDT"

# Web roots that nginx / Next serve statically. Missing paths are skipped so the
# same feed script is safe on any host.
TARGET_ROOTS = [
    "/var/www/animica.org",
    "/var/www/animica.dev",
    "/var/www/academy.animica.org",
    "/var/www/studio.animica.org",
    "/root/animica/animica-pool/apps/web/public",
    "/root/animica/apps/animica-xyz/public",
    "/root/animica/explorer2/web/public",
]

OUT_NAME = "anm-price.json"
UA = "animica-price-feed/1.0 (+https://animica.org)"


def _fetch(url: str) -> dict:
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=15) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _f(v, default=0.0) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def build_payload() -> dict:
    t = _fetch(TICKER_URL)
    # market/getbysymbol adds high/low/volume; tolerate its absence.
    try:
        m = _fetch(MARKET_URL)
    except Exception:
        m = {}

    last = _f(t.get("last_price"))
    bid = _f(t.get("bid"))
    ask = _f(t.get("ask"))
    mid = (bid + ask) / 2 if (bid > 0 and ask > 0) else 0.0
    display = last if last > 0 else mid
    return {
        "symbol": "ANM/USDT",
        "base": "ANM",
        "quote": "USDT",
        "last": last,
        "bid": bid,
        "ask": ask,
        "mid": round(mid, 10),
        "display": round(display, 10),
        "is_indicative": last <= 0,  # no trades yet → showing bid/ask mid
        "change_percent": _f(t.get("change_percent")),
        "base_volume": _f(t.get("base_volume")),
        "target_volume": _f(t.get("target_volume")),
        "high": _f(m.get("highPrice")),
        "low": _f(m.get("lowPrice")),
        "market_url": MARKET_LINK,
        "pool_url": POOL_LINK,
        "source": "nonkyc",
        "ts": int(time.time()),
    }


def publish(payload: dict) -> int:
    blob = json.dumps(payload, separators=(",", ":"))
    written = 0
    for root in TARGET_ROOTS:
        if not os.path.isdir(root):
            continue
        dst = os.path.join(root, OUT_NAME)
        tmp = dst + ".tmp"
        try:
            with open(tmp, "w", encoding="utf-8") as fh:
                fh.write(blob)
            os.replace(tmp, dst)  # atomic
            try:
                os.chmod(dst, 0o644)
            except OSError:
                pass
            written += 1
        except OSError as exc:
            print(f"warn: could not write {dst}: {exc}")
    return written


def main() -> int:
    try:
        payload = build_payload()
    except Exception as exc:
        # Leave existing (last-good) files untouched on any fetch/parse error.
        print(f"error: fetch failed, keeping last-good files: {exc}")
        return 1
    n = publish(payload)
    print(f"published anm-price.json to {n} root(s): last={payload['last']} "
          f"mid={payload['mid']} indicative={payload['is_indicative']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
