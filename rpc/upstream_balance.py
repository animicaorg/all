"""Authoritative balance/nonce read-through for un-synced nodes.

When the local node is behind a trusted upstream (still syncing, stalled, or
freshly bootstrapped), reading account state locally returns stale/zero values —
an exchange then sees a funded address as empty. This module lets the
client-facing state read RPCs (`state.getBalance`, `state.getNonce`, and the
Bitcoin-compat `getbalance`) return the AUTHORITATIVE value from a trusted,
already-synced upstream node whenever the local node is behind.

Trust model & safety:
  * Activates ONLY when the local node is provably behind a SAME-CHAIN upstream:
    we must know the local chain id AND the local height, the upstream must be
    the same chain id, and it must be ahead by at least ANIMICA_BALANCE_UPSTREAM_MIN_LAG
    blocks. If any of those are unknown, we return the local value. A fully
    synced node never consults the upstream for a value.
  * Consensus, execution and transaction validation ALWAYS use local state —
    this is only a convenience for the client-facing *read* RPCs.
  * Fails safe: any upstream error/timeout returns the local value. Both the
    upstream head AND per-address values (incl. failures) are cached, so a
    slow/down upstream cannot add latency to more than one request per TTL per
    key. HTTP redirects are disabled and only http/https URLs are accepted.

Configuration (env):
  ANIMICA_BALANCE_UPSTREAM_RPC   comma-separated upstream RPC URLs.
                                 Default on mainnet (chain_id 1):
                                   https://rpc.animica.org/rpc,https://mainnet.animica.org/rpc
                                 No default on other chains. Point this at your
                                 OWN synced node if you'd rather not trust the
                                 public RPC.
  ANIMICA_BALANCE_UPSTREAM=off   disable entirely (also: 0/false/no/disabled).
  ANIMICA_BALANCE_UPSTREAM_MIN_LAG   blocks behind before read-through engages
                                     (default 16).
"""

from __future__ import annotations

import json
import os
import threading
import time
import urllib.request
from urllib.parse import urlparse

_DEFAULT_MAINNET = "https://rpc.animica.org/rpc,https://mainnet.animica.org/rpc"
_HEAD_TTL = 20.0        # cache upstream head (success AND failure)
_VALUE_TTL = 8.0        # cache per-(url,method,address) value (success AND failure)
_HEAD_TIMEOUT = 3.0
_VALUE_TIMEOUT = 4.0
_DEFAULT_MIN_LAG = 16

_lock = threading.RLock()
_head_cache = {"t": -1e9, "checked": False, "height": None, "chain_id": None, "url": None}
_value_cache: dict = {}   # key -> (monotonic_ts, ok: bool, value: int|None)

# Opener that refuses to follow redirects (an upstream must not be able to bounce
# our request toward an internal/metadata address).
class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: D401
        return None


_OPENER = urllib.request.build_opener(_NoRedirect)


def _enabled() -> bool:
    v = (os.environ.get("ANIMICA_BALANCE_UPSTREAM") or "").strip().lower()
    return v not in ("0", "off", "false", "no", "disable", "disabled")


def _urls(local_chain_id) -> list:
    raw = os.environ.get("ANIMICA_BALANCE_UPSTREAM_RPC")
    if raw is None:
        try:
            if int(local_chain_id or 0) == 1:
                raw = _DEFAULT_MAINNET
            else:
                return []
        except Exception:
            return []
    raw = raw.strip()
    if raw.lower() in ("", "off", "none", "disable", "disabled"):
        return []
    out = []
    for u in raw.split(","):
        u = u.strip()
        if not u:
            continue
        try:
            scheme = urlparse(u).scheme.lower()
        except Exception:
            continue
        if scheme not in ("http", "https"):
            continue  # only http(s); no file:// etc.
        out.append(u)
    return out


def _min_lag() -> int:
    try:
        return max(1, int(os.environ.get("ANIMICA_BALANCE_UPSTREAM_MIN_LAG", str(_DEFAULT_MIN_LAG))))
    except Exception:
        return _DEFAULT_MIN_LAG


def _int_from(v) -> int:
    if isinstance(v, bool):
        raise ValueError("bool not a quantity")
    if isinstance(v, str):
        s = v.strip()
        return int(s, 16) if s.lower().startswith("0x") else int(s)
    return int(v)


def _post(url: str, method: str, params: list, timeout: float):
    body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params}).encode()
    req = urllib.request.Request(url, data=body, headers={"content-type": "application/json"})
    with _OPENER.open(req, timeout=timeout) as resp:  # nosec - operator-configured, no-redirect, http(s) only
        data = json.loads(resp.read().decode())
    if isinstance(data, dict) and data.get("error"):
        raise RuntimeError(str(data["error"]))
    return (data or {}).get("result")


def _upstream_head(urls: list):
    """(height, chain_id, url) of the first responsive upstream, cached _HEAD_TTL
    seconds. Failures are cached (as None) so a down upstream can't be re-probed
    on every request."""
    now = time.monotonic()
    with _lock:
        if _head_cache["checked"] and (now - _head_cache["t"]) < _HEAD_TTL:
            return _head_cache["height"], _head_cache["chain_id"], _head_cache["url"]
    height = cid = url = None
    for u in urls:
        try:
            h = _post(u, "chain.getHead", [], _HEAD_TIMEOUT) or {}
            height = _int_from(h.get("height", h.get("number")))
            cid = _int_from(h.get("chainId", h.get("chain_id", 0)))
            url = u
            break
        except Exception:
            continue
    with _lock:
        _head_cache.update(t=time.monotonic(), checked=True, height=height, chain_id=cid, url=url)
    return height, cid, url


def _upstream_value(url: str, method: str, params: list) -> int:
    """Cached upstream value fetch. Caches successes AND failures for _VALUE_TTL
    so a slow/down upstream (that nonetheless passed the head check) can't add
    latency to more than one request per key per TTL. Raises on a cached/live
    failure so the caller falls back to local."""
    key = (url, method, tuple(str(p) for p in params))
    now = time.monotonic()
    with _lock:
        hit = _value_cache.get(key)
        if hit is not None and (now - hit[0]) < _VALUE_TTL:
            if hit[1]:
                return hit[2]
            raise RuntimeError("cached upstream failure")
    try:
        val = _int_from(_post(url, method, params, _VALUE_TIMEOUT))
        with _lock:
            _value_cache[key] = (time.monotonic(), True, val)
            if len(_value_cache) > 50000:   # bound memory
                _value_cache.clear()
        return val
    except Exception:
        with _lock:
            _value_cache[key] = (time.monotonic(), False, None)
        raise


def authoritative(address: str, method: str, local_value_int: int,
                  local_height, local_chain_id, extra_params=None) -> int:
    """Return the authoritative integer for `method`(address).

    `method` is 'state.getBalance' (nANM) or 'state.getNonce'. Returns the LOCAL
    value unless the local node is provably behind a same-chain upstream, in
    which case it returns the upstream's value. Any doubt or error -> local.
    """
    try:
        local_value_int = int(local_value_int)
    except Exception:
        local_value_int = 0
    try:
        if not _enabled():
            return local_value_int
        # Must know BOTH the local chain id and height to prove we're behind and
        # same-chain; unknown -> never read-through.
        if local_chain_id is None or local_height is None:
            return local_value_int
        urls = _urls(local_chain_id)
        if not urls:
            return local_value_int
        up_h, up_cid, up_url = _upstream_head(urls)
        if up_h is None or up_url is None:
            return local_value_int
        # Same chain only.
        if up_cid is None or int(up_cid) != int(local_chain_id):
            return local_value_int
        # Local caught up enough -> trust local (no read-through).
        if up_h <= int(local_height) + _min_lag():
            return local_value_int
        params = [address] + list(extra_params or [])
        return _upstream_value(up_url, method, params)
    except Exception:
        return local_value_int


def status(local_height=None, local_chain_id=None) -> dict:
    """Introspection for diagnostics: is read-through active and to where."""
    urls = _urls(local_chain_id)
    out = {"enabled": _enabled(), "urls": urls, "min_lag": _min_lag(),
           "upstream_height": None, "upstream_chain_id": None, "upstream_url": None,
           "engaged": False}
    if not out["enabled"] or not urls:
        return out
    up_h, up_cid, up_url = _upstream_head(urls)
    out["upstream_height"] = up_h
    out["upstream_chain_id"] = up_cid
    out["upstream_url"] = up_url
    if (up_h is not None and local_height is not None and local_chain_id is not None
            and up_cid is not None and int(up_cid) == int(local_chain_id)):
        out["engaged"] = bool(up_h > int(local_height) + _min_lag())
    return out
