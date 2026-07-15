"""Directory / aggregator listing engine.

Auto-fills an ACCURATE Animica listing application and, for targets that expose a sanctioned
programmatic submission endpoint, submits it; for targets that gate submission behind a human /
CAPTCHA / account (which is most major aggregators), it prepares a complete, ready-to-paste
application and QUEUES it for a person to submit. It never bypasses CAPTCHAs/anti-bot controls,
never creates fake accounts, and reports every outcome honestly.
"""

from __future__ import annotations

import json
import os
import time
import urllib.request
from typing import List, Optional

from .config import GrowthConfig
from . import store
from .collectors import _get_json, _rpc, _num


# Real listing targets. method: api = sanctioned programmatic endpoint; form/manual = human-submitted
# (we prepare + queue). automation_allowed is False unless the site sanctions automated submission —
# so by default we DO NOT fire at anti-bot forms (that gets applications blacklisted).
TARGETS = [
    {"name": "CoinGecko", "site": "https://www.coingecko.com", "submit": "https://www.coingecko.com/en/coins/new", "method": "form", "automation_allowed": False},
    {"name": "CoinMarketCap", "site": "https://coinmarketcap.com", "submit": "https://support.coinmarketcap.com/hc/en-us/requests/new", "method": "form", "automation_allowed": False},
    {"name": "CoinPaprika", "site": "https://coinpaprika.com", "submit": "https://coinpaprika.com/submit-coin/", "method": "form", "automation_allowed": False},
    {"name": "LiveCoinWatch", "site": "https://www.livecoinwatch.com", "submit": "https://www.livecoinwatch.com/submit", "method": "form", "automation_allowed": False},
    {"name": "CoinCodex", "site": "https://coincodex.com", "submit": "https://coincodex.com/page/add-coin/", "method": "form", "automation_allowed": False},
    {"name": "CryptoCompare", "site": "https://www.cryptocompare.com", "submit": "https://www.cryptocompare.com/coins/submit/", "method": "form", "automation_allowed": False},
    {"name": "Coinranking", "site": "https://coinranking.com", "submit": "https://coinranking.com/submit-coin", "method": "form", "automation_allowed": False},
    {"name": "DappRadar", "site": "https://dappradar.com", "submit": "https://dappradar.com/submit-dapp", "method": "form", "automation_allowed": False},
    {"name": "CoinCap", "site": "https://coincap.io", "submit": "https://coincap.io/", "method": "manual", "automation_allowed": False},
]


def animica_listing_data(cfg: GrowthConfig) -> dict:
    """The accurate application payload. Pulls live supply/price where available; leaves unknown
    fields blank rather than fabricating them."""
    price = _get_json(cfg.nonkyc_ticker)
    last = None
    if isinstance(price, dict):
        last = price.get("last_price") or price.get("lastPrice") or price.get("last")
    head = _rpc(cfg.rpc_url, "chain.getHead")
    height = _num(head, "height") if isinstance(head, dict) else None

    return {
        "name": "Animica",
        "symbol": "ANM",
        "type": "Layer-1 coin (post-quantum)",
        "description": (
            "Animica is a post-quantum Layer-1 blockchain (ML-DSA-65 signatures) with a free, "
            "keyless OpenAI-compatible AI network, a decentralized AI marketplace, generative media "
            "served by GPU miners, a decentralized VPN, and a sovereign .anm internet."
        ),
        "website": "https://animica.dev",
        "homepage_alt": "https://animica.org",
        "explorer": "https://explorer.animica.org",
        "github": "https://github.com/animicaorg/all",
        "docs": "https://animica.dev/docs",
        "discord": cfg.discord_invite,
        "whitepaper": "https://animica.org",
        "logo": "https://animica.dev/animica-mark.svg",
        "og_image": "https://animica.dev/og.png",
        "markets": [{"exchange": "NonKYC", "pair": "ANM/USDT", "url": cfg.nonkyc_market}],
        "price_usdt": last,          # None if unavailable — never fabricated
        "chain_height": height,      # None if RPC unreachable
        "tags": ["layer-1", "post-quantum", "ai", "defi", "pow"],
        "prepared_at": int(time.time()),
    }


def prepare(cfg: GrowthConfig) -> dict:
    """Build the application + per-target instructions and write a kit file. No network side effects
    beyond reading public price/height for accuracy."""
    data = animica_listing_data(cfg)
    apps = []
    for t in TARGETS:
        apps.append({
            "target": t["name"], "submit_url": t["submit"], "method": t["method"],
            "automation_allowed": t["automation_allowed"],
            "action": ("auto-submit" if (t["automation_allowed"]) else "prepared — submit at submit_url"),
            "application": data,
        })
    os.makedirs(cfg.state_dir, exist_ok=True)
    kit_path = os.path.join(cfg.state_dir, "listing-kit.json")
    with open(kit_path, "w") as f:
        json.dump({"data": data, "targets": apps}, f, indent=2)
    return {"data": data, "targets": apps, "kit_path": kit_path,
            "auto": sum(1 for a in apps if a["automation_allowed"]),
            "queued": sum(1 for a in apps if not a["automation_allowed"])}


def submit(cfg: GrowthConfig, *, do_submit: bool = False, log=print) -> dict:
    """Submit to targets that sanction automated submission; queue the rest. do_submit=False (default)
    is a dry-run preview. Honest audit log to the store either way."""
    prep = prepare(cfg)
    submitted, queued, failed = 0, 0, 0
    for a in prep["targets"]:
        name = a["target"]
        if a["automation_allowed"] and a.get("endpoint"):
            if not do_submit:
                log(f"  [would auto-submit] {name} -> {a['submit_url']}")
                store.log_listing(cfg, name, a["method"], "DRY_RUN_AUTO", a["submit_url"])
                continue
            try:
                body = json.dumps(a["application"]).encode()
                req = urllib.request.Request(a["endpoint"], data=body,
                                             headers={"Content-Type": "application/json"}, method="POST")
                with urllib.request.urlopen(req, timeout=20) as r:  # nosec B310
                    code = r.getcode()
                store.log_listing(cfg, name, a["method"], f"SUBMITTED_{code}", a["endpoint"])
                submitted += 1
                log(f"  ✓ submitted to {name} ({code})")
            except Exception as e:
                store.log_listing(cfg, name, a["method"], "FAILED", str(e)[:120])
                failed += 1
                log(f"  ✗ {name}: {e}")
        else:
            # Human-gated (CAPTCHA/account/form) — never auto-fired. Prepared + queued.
            store.log_listing(cfg, name, a["method"], "QUEUED_MANUAL", a["submit_url"])
            queued += 1
            log(f"  • queued for you: {name} — submit the prepared application at {a['submit_url']}")
    return {"submitted": submitted, "queued": queued, "failed": failed,
            "kit_path": prep["kit_path"], "note":
            "Major aggregators require a human/CAPTCHA form submission; those are prepared + queued, "
            "not auto-fired (that would get the applications blacklisted)."}
