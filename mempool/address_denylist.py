"""Operator address freeze-list (incident response).

Reject any transaction whose sender OR recipient is a listed 32-byte account
digest. This lets an operator FREEZE a stolen-key victim wallet (so no more can
be drained from it) and a thief's collection address (so stolen funds cannot be
moved on) without waiting on a code release.

Sources (union), reloaded at most every ``_RELOAD_INTERVAL_S`` so an operator
can freeze/unfreeze live by editing the file (no node restart):

  * env ``ANIMICA_ADDRESS_DENYLIST`` — comma/space-separated hex digests
    (optional ``0x``) or ``anim1…`` addresses.
  * JSON file ``ANIMICA_ADDRESS_DENYLIST_FILE`` (default
    ``/data/.animica/address_denylist.json``), shape:
    ``{"addresses": ["0x<digest>", "anim1…", …]}`` (or a bare JSON list).

Scope: this is a MEMPOOL / block-template control — it stops the operator's own
nodes from admitting or mining the listed txs. It is NOT a consensus rule (a
block that already contains such a tx still validates), so it freezes funds only
to the extent the operator controls block production. For a hard, network-wide
freeze the same check must run in block validation behind a coordinated upgrade.
"""
from __future__ import annotations

import json
import os
import time
from typing import Optional, Set

_ENV = "ANIMICA_ADDRESS_DENYLIST"
_ENV_FILE = "ANIMICA_ADDRESS_DENYLIST_FILE"
_DEFAULT_FILE = "/data/.animica/address_denylist.json"
_RELOAD_INTERVAL_S = 15.0

_cache: Set[bytes] = set()
_cache_at: float = 0.0


def _norm(token: str) -> Optional[bytes]:
    """Normalize a hex digest or anim1 address to a 32-byte account key."""
    s = token.strip()
    if not s:
        return None
    if s.startswith("anim1"):
        try:
            from pq.py.address import decode_address

            return bytes(decode_address(s).digest)[:32].ljust(32, b"\x00")
        except Exception:
            return None
    if s.lower().startswith("0x"):
        s = s[2:]
    try:
        b = bytes.fromhex(s)
    except ValueError:
        return None
    return b[:32].ljust(32, b"\x00") if b else None


def _load() -> Set[bytes]:
    out: Set[bytes] = set()
    for tok in os.environ.get(_ENV, "").replace(",", " ").split():
        b = _norm(tok)
        if b:
            out.add(b)
    path = os.environ.get(_ENV_FILE, _DEFAULT_FILE)
    try:
        with open(path) as fh:
            doc = json.load(fh)
    except Exception:
        doc = None
    if doc is not None:
        items = doc.get("addresses", []) if isinstance(doc, dict) else doc
        if isinstance(items, list):
            for tok in items:
                if isinstance(tok, str):
                    b = _norm(tok)
                    if b:
                        out.add(b)
    return out


def denied_addresses(*, now: Optional[float] = None) -> Set[bytes]:
    global _cache, _cache_at
    t = now if now is not None else time.time()
    if not _cache_at or (t - _cache_at) >= _RELOAD_INTERVAL_S:
        _cache = _load()
        _cache_at = t
    return _cache


def is_denied(addr: Optional[bytes], *, now: Optional[float] = None) -> bool:
    if not addr:
        return False
    try:
        a = bytes(addr)[:32].ljust(32, b"\x00")
    except Exception:
        return False
    return a in denied_addresses(now=now)


def reload_now() -> Set[bytes]:
    """Force an immediate reload (test/ops helper)."""
    global _cache_at
    _cache_at = 0.0
    return denied_addresses()
