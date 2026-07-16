"""Config + live-data providers for the stream worker.

- `default_net_provider()` feeds the HUD/brain real network stats (height, peers, price).
- `load_character()` builds the live-editable Character from the console (or defaults).
- `build_config()` assembles a StreamConfig from CLI args / env.
"""
from __future__ import annotations

import json
import os
import urllib.request
from typing import Optional

from .contract import Character, StreamConfig

_RPC = os.environ.get("ANIMICA_RPC_URL", "https://rpc.animica.org/rpc")
_PRICE_URLS = [
    "https://animica.dev/anm-price.json",
    "https://animica.org/anm-price.json",
]


def _rpc(method: str, params=None, timeout: float = 5.0):
    body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params or []}).encode()
    req = urllib.request.Request(_RPC, data=body, headers={"content-type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read()).get("result")


_price_cache = {"t": 0.0, "v": None}


def _price():
    import time
    if _price_cache["v"] is not None and time.time() - _price_cache["t"] < 120:
        return _price_cache["v"]
    for u in _PRICE_URLS:
        try:
            with urllib.request.urlopen(u, timeout=4) as r:
                d = json.loads(r.read())
            v = d.get("price_usd") or d.get("price") or d.get("last")
            if v:
                _price_cache.update(t=time.time(), v=str(v))
                return str(v)
        except Exception:
            continue
    return None


def default_net_provider():
    """Best-effort live network stats for the overlay + brain grounding. Never raises."""
    stats = {}
    try:
        head = _rpc("chain.getHead")
        if isinstance(head, dict):
            stats["height"] = head.get("height") or head.get("number")
        elif isinstance(head, int):
            stats["height"] = head
    except Exception:
        pass
    try:
        h = _rpc("system.health")
        if isinstance(h, dict):
            stats["peers"] = h.get("peers") or h.get("connectedPeers")
    except Exception:
        pass
    p = _price()
    if p:
        stats["price"] = p
    return stats


def load_character(mkt_url: str = "", internal_token: str = "") -> Character:
    """Load the live-editable character sheet from the console's internal state, else default.
    The Animica default is the programmatic cat; end users' sheets override it."""
    mkt_url = mkt_url or os.environ.get("ANIMICA_MKT_URL", "http://127.0.0.1:4950")
    token = internal_token or os.environ.get("ANIMAL_INTERNAL_TOKEN", "")
    if not token:
        return Character()
    try:
        req = urllib.request.Request(
            mkt_url.rstrip("/") + "/api/mkt/v1/animal/internal/state",
            headers={"authorization": f"Bearer {token}"})
        with urllib.request.urlopen(req, timeout=8) as r:
            d = json.loads(r.read())
        char = Character.from_dict(d.get("character") or {})
        _resolve_sprite(char)
        return char
    except Exception:
        return Character()


def _resolve_sprite(char: Character) -> None:
    """If the character is a custom PNG, download it once to a local file so the
    SpriteActor can render it. Best-effort: on any failure the actor falls back to the cat."""
    if getattr(char, "kind", "cat") != "sprite":
        return
    for attr in ("sprite_url", "sprite_open_url"):
        url = getattr(char, attr, "") or ""
        path_attr = "sprite_path" if attr == "sprite_url" else "sprite_open_path"
        if not url or getattr(char, path_attr, ""):
            continue
        if not (url.startswith("http://") or url.startswith("https://")):
            continue
        try:
            import hashlib
            import tempfile
            key = hashlib.sha256(url.encode()).hexdigest()[:16]
            dst = os.path.join(tempfile.gettempdir(), f"anm-sprite-{key}.png")
            if not os.path.exists(dst):
                req = urllib.request.Request(url, headers={"user-agent": "animica-animal/stream"})
                with urllib.request.urlopen(req, timeout=15) as r:
                    data = r.read(8 * 1024 * 1024 + 1)
                if len(data) > 8 * 1024 * 1024 or not data.startswith(b"\x89PNG\r\n\x1a\n"):
                    continue  # size guard + PNG magic; ignore anything else
                with open(dst, "wb") as f:
                    f.write(data)
            setattr(char, path_attr, dst)
        except Exception:
            pass


def build_config(**kw) -> StreamConfig:
    cfg = StreamConfig()
    for k, v in kw.items():
        if v is not None and hasattr(cfg, k):
            setattr(cfg, k, v)
    return cfg
