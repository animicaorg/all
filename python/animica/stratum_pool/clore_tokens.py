"""Serve a Clore onboarding config to consenting miners, priced competitively
for each miner's actual GPU.

Clore's onboarding config is ACCOUNT-level (one `auth` key enrolls any machine),
but the `autoprice` inside it is PER-MACHINE. A flat price is wrong: at $5/day a
3070 (market ~$0.74) never rents and earns nothing. So this module keeps the
operator's base config (the auth key + rental settings) and OVERWRITES autoprice
with a live, GPU-specific figure derived from what comparable machines that are
ACTUALLY RENTED charge on the Clore marketplace right now.

Pricing rule: take the median USD/day of *rented* single-GPU-equivalent listings
of the same model, then shave a small margin so this host undercuts and actually
rents (an idle GPU at the median earns $0; a rented GPU just under it earns most
of the median). Falls back to the whole-listing median, then to the base config's
price, if the market can't be read.

Base config source (first wins):
  * env  ANIMICA_CLORE_ONBOARDING  — the base64 --onboarding-config blob
  * file ANIMICA_CLORE_ONBOARDING_FILE (default /etc/animica/clore-onboarding.txt)

Returns None when unset — the 10.2.8+ miner client then skips enrollment cleanly.
"""
from __future__ import annotations

import base64
import json
import os
import re
import time
import urllib.request
from pathlib import Path
from typing import Optional

# Undercut the rented-median by this much so the host actually rents.
UNDERCUT = float(os.getenv("ANIMICA_CLORE_UNDERCUT", "0.92"))
_MK_URL = "https://api.clore.ai/v1/marketplace"
_MK_KEY = os.getenv("CLORE_API_KEY", "")
_cache = {"ts": 0.0, "by_model": {}}


def _base_config() -> Optional[str]:
    v = os.getenv("ANIMICA_CLORE_ONBOARDING", "").strip()
    if not v:
        try:
            v = Path(os.getenv("ANIMICA_CLORE_ONBOARDING_FILE",
                               "/etc/animica/clore-onboarding.txt")).read_text().strip()
        except Exception:
            v = ""
    return v or None


def _decode(cfg: str) -> Optional[dict]:
    try:
        obj = json.loads(base64.b64decode(cfg))
        return obj if isinstance(obj, dict) and obj.get("auth") else None
    except Exception:
        return None


def _encode(obj: dict) -> str:
    return base64.b64encode(json.dumps(obj).encode()).decode()


def _norm_gpu(name: str) -> str:
    """Collapse a GPU string to a comparable model key, e.g. 'RTX 4090'."""
    s = (name or "").upper()
    m = re.search(r"(RTX|GTX)\s*([0-9]{3,4})\s*(TI|SUPER)?", s)
    if m:
        return f"{m.group(1)} {m.group(2)}{(' ' + m.group(3)) if m.group(3) else ''}".strip()
    m = re.search(r"\b([A-Z]?[0-9]{2,4}[A-Z]?)\b", s)  # A100, H100, L40, etc.
    return m.group(1) if m else s.strip()


def _market() -> dict:
    """{model_key: median_rented_usd_per_gpu_day}. Cached 5 min."""
    if time.time() - _cache["ts"] < 300 and _cache["by_model"]:
        return _cache["by_model"]
    by: dict = {}
    try:
        req = urllib.request.Request(_MK_URL, headers={
            "User-Agent": "Mozilla/5.0 animica-pool/1.0", "auth": _MK_KEY})
        with urllib.request.urlopen(req, timeout=15) as r:
            servers = json.loads(r.read().decode()).get("servers", [])
        buckets: dict = {}
        for s in servers:
            g = (s.get("specs") or {}).get("gpu") or ""
            m = re.match(r"(\d+)x\s+(.*)", g)
            if not m:
                continue
            n, model = int(m.group(1)), _norm_gpu(m.group(2))
            usd = ((s.get("price") or {}).get("on_demand") or {}).get("USD-Blockchain")
            if not usd or n <= 0:
                continue
            buckets.setdefault(model, {"rented": [], "all": []})
            per = usd / n
            buckets[model]["all"].append(per)
            if s.get("rented"):
                buckets[model]["rented"].append(per)
        for model, b in buckets.items():
            pool = sorted(b["rented"] or b["all"])
            if pool:
                by[model] = pool[len(pool) // 2]  # median
        _cache.update(ts=time.time(), by_model=by)
    except Exception:
        pass
    return by


def competitive_price(gpu: str) -> Optional[float]:
    """Live competitive USD/day for one GPU of this model, or None if unknown."""
    key = _norm_gpu(gpu)
    med = _market().get(key)
    return round(med * UNDERCUT, 2) if med else None


def assign_token(worker: str, address: str, gpu: str = "") -> Optional[str]:
    """Return a Clore onboarding config priced for this miner's GPU.

    Keeps the operator's base config (auth + rental settings), replaces autoprice
    with a live, model-specific, competitive figure. If the GPU is unknown or the
    market unreadable, the base config's own price is used unchanged.
    """
    base = _base_config()
    obj = _decode(base) if base else None
    if obj is None:
        return None
    price = competitive_price(gpu) if gpu else None
    if price and price > 0:
        obj = dict(obj)
        obj["autoprice"] = {"usd": True, "on_demand": price, "spot": price}
    return _encode(obj)


def stats() -> dict:
    base = _base_config()
    return {"configured": bool(base), "valid": bool(base and _decode(base)),
            "models_priced": len(_market())}
