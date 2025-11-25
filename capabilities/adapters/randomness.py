"""
capabilities.adapters.randomness
===============================

Small stdlib-only adapter for reading the randomness beacon from a running
Animica node (via JSON-RPC) or from a provided in-process service object.

Supported backends (tried in this order):

  1) *service* object (duck-typed) exposing:
       - get_beacon(round_id: int | None) -> dict
       - get_round() -> dict
       - get_history(limit: int = 10, before_round: int | None = None) -> list[dict]
     Accepts camelCase alternatives: getBeacon / getRound / getHistory.

  2) JSON-RPC endpoint exposing methods:
       - "rand.getBeacon"   (params: {"round": <int>} or {})
       - "rand.getRound"    (params: {})
       - "rand.getHistory"  (params: {"limit": <int>, "before": <int>})

Environment configuration (if endpoint/api_key not supplied to the ctor):

  - RANDOMNESS_RPC_URL or RAND_ENDPOINT  → JSON-RPC base URL
  - RAND_API_KEY                         → optional Bearer token
  - RAND_TIMEOUT_S                       → float seconds (default 10.0)

Return shapes (normalized):

  get_beacon(...) -> {
      "round": int,
      "output": "0x..."    # hex string (lowercase, even-length, with 0x)
      "provider": "service" | "rpc",
      "details": dict      # backend-specific extras (may include vdf/aggregate, etc.)
  }

  get_round() -> {
      ... # passthrough fields from backend; at least "current" or "round"
      "provider": "service" | "rpc"
  }

  get_history(...) -> {
      "items": [ <normalized beacons as in get_beacon> ],
      "provider": "service" | "rpc"
  }

Convenience:

  latest_output_bytes() -> bytes        # just the beacon output bytes of the latest round
  beacon_output_bytes(obj) -> bytes     # decode a normalized beacon dict

This module keeps dependencies minimal by using urllib from the Python stdlib.
"""

from __future__ import annotations

import binascii
import json
import os
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Union
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

HexLike = Union[str, bytes, bytearray, memoryview]

__all__ = [
    "RandomnessAdapter",
    "get_beacon",
    "get_round",
    "get_history",
    "latest_output_bytes",
    "beacon_output_bytes",
]


# ---------------------------------------------------------------------------
# Utilities
# ---------------------------------------------------------------------------

def _ensure_hex_0x(x: HexLike) -> str:
    if isinstance(x, (bytes, bytearray, memoryview)):
        return "0x" + bytes(x).hex()
    s = str(x).strip().lower()
    if s.startswith("0x"):
        s = s[2:]
    # validate hex and normalize even length
    if len(s) % 2 == 1:
        s = "0" + s
    try:
        _ = binascii.unhexlify(s)
    except binascii.Error as e:
        raise ValueError(f"Invalid hex string: {x!r}") from e
    return "0x" + s

def _hex_to_bytes(x: HexLike) -> bytes:
    if isinstance(x, (bytes, bytearray, memoryview)):
        return bytes(x)
    s = str(x).strip().lower()
    if s.startswith("0x"):
        s = s[2:]
    if len(s) % 2 == 1:
        s = "0" + s
    try:
        return binascii.unhexlify(s)
    except binascii.Error as e:
        raise ValueError(f"Invalid hex string: {x!r}") from e


# ---------------------------------------------------------------------------
# Minimal JSON-RPC shim (stdlib only)
# ---------------------------------------------------------------------------

@dataclass
class _JsonRpc:
    url: str
    api_key: Optional[str] = None
    timeout_s: float = 10.0

    def call(self, method: str, params: Optional[dict] = None) -> Any:
        payload = {"jsonrpc": "2.0", "id": "rand", "method": method, "params": params or {}}
        data = json.dumps(payload).encode("utf-8")
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        req = Request(self.url, data=data, headers=headers, method="POST")
        try:
            with urlopen(req, timeout=self.timeout_s) as resp:
                raw = resp.read()
        except HTTPError as e:
            body = ""
            try:
                body = e.read().decode("utf-8", "ignore")
            except Exception:
                pass
            raise RuntimeError(f"HTTP {e.code} {method}: {body}") from e
        except URLError as e:
            raise RuntimeError(f"Network error {method}: {e}") from e

        try:
            obj = json.loads(raw.decode("utf-8"))
        except Exception as e:
            preview = raw[:256]
            raise RuntimeError(f"Non-JSON response from RPC: {preview!r}") from e

        if "error" in obj:
            err = obj["error"]
            raise RuntimeError(f"RPC error {err.get('code')}: {err.get('message')}")
        return obj.get("result")


# ---------------------------------------------------------------------------
# Adapter
# ---------------------------------------------------------------------------

class RandomnessAdapter:
    """
    Read the randomness beacon via a service object or JSON-RPC.
    """

    def __init__(
        self,
        *,
        service: Optional[Any] = None,
        endpoint: Optional[str] = None,
        api_key: Optional[str] = None,
        timeout_s: Optional[float] = None,
    ):
        self.service = service
        # Resolve config from env if not provided
        ep_env = (
            endpoint
            or os.getenv("RANDOMNESS_RPC_URL")
            or os.getenv("RAND_ENDPOINT")
        )
        key_env = api_key or os.getenv("RAND_API_KEY")
        t_s = float(timeout_s if timeout_s is not None else os.getenv("RAND_TIMEOUT_S", "10.0"))
        self.rpc = _JsonRpc(ep_env, key_env, t_s) if ep_env else None

    # Public methods --------------------------------------------------------

    def get_beacon(self, round_id: Optional[int] = None) -> Dict[str, Any]:
        """
        Fetch a beacon. If round_id is None, returns the latest beacon.
        """
        # 1) Service
        if self.service is not None:
            for name in ("get_beacon", "getBeacon"):
                fn = getattr(self.service, name, None)
                if callable(fn):
                    res = fn(round_id) if round_id is not None else fn()
                    return _normalize_beacon(res, provider="service")

        # 2) JSON-RPC
        if self.rpc:
            params = {}
            if round_id is not None:
                params["round"] = int(round_id)
            res = self.rpc.call("rand.getBeacon", params)
            return _normalize_beacon(res, provider="rpc")

        raise RuntimeError("No randomness backend available (service and endpoint both missing).")

    def get_round(self) -> Dict[str, Any]:
        """
        Fetch current round status/metadata (passthrough + provider tag).
        """
        # 1) Service
        if self.service is not None:
            for name in ("get_round", "getRound"):
                fn = getattr(self.service, name, None)
                if callable(fn):
                    res = fn()
                    out = dict(res) if isinstance(res, dict) else {"round": res}
                    out["provider"] = "service"
                    return out

        # 2) JSON-RPC
        if self.rpc:
            res = self.rpc.call("rand.getRound", {})
            out = dict(res) if isinstance(res, dict) else {"round": res}
            out["provider"] = "rpc"
            return out

        raise RuntimeError("No randomness backend available (service and endpoint both missing).")

    def get_history(self, *, limit: int = 10, before_round: Optional[int] = None) -> Dict[str, Any]:
        """
        Fetch a list of recent beacons.
        """
        # 1) Service
        if self.service is not None:
            for name in ("get_history", "getHistory"):
                fn = getattr(self.service, name, None)
                if callable(fn):
                    items = fn(limit=limit, before_round=before_round)  # type: ignore[misc]
                    return {
                        "items": [_normalize_beacon(x, provider="service") for x in items],
                        "provider": "service",
                    }

        # 2) JSON-RPC
        if self.rpc:
            params = {"limit": int(limit)}
            if before_round is not None:
                params["before"] = int(before_round)
            res = self.rpc.call("rand.getHistory", params)
            items = res if isinstance(res, list) else []
            return {
                "items": [_normalize_beacon(x, provider="rpc") for x in items],
                "provider": "rpc",
            }

        raise RuntimeError("No randomness backend available (service and endpoint both missing).")


# ---------------------------------------------------------------------------
# Normalization
# ---------------------------------------------------------------------------

def _normalize_beacon(obj: Any, *, provider: str) -> Dict[str, Any]:
    """
    Normalize various beacon shapes into:
      { "round": int, "output": "0x..", "provider": provider, "details": {...} }
    """
    if not isinstance(obj, dict):
        # Try to interpret as {"output": <obj>}
        return {
            "round": -1,
            "output": _ensure_hex_0x(obj),
            "provider": provider,
            "details": {},
        }

    # Heuristics for common shapes
    round_id = obj.get("round") or obj.get("id") or obj.get("height") or -1
    out = obj.get("output") or obj.get("beacon") or obj.get("value") or obj.get("digest")

    if out is None:
        # Look for nested "beacon": {"output": "..."}
        b = obj.get("beacon")
        if isinstance(b, dict) and "output" in b:
            out = b["output"]

    if out is None:
        raise ValueError(f"Beacon object missing 'output': {obj!r}")

    normalized = {
        "round": int(round_id),
        "output": _ensure_hex_0x(out),
        "provider": provider,
        "details": {k: v for k, v in obj.items() if k not in ("round", "id", "height", "output", "beacon", "value", "digest")},
    }
    return normalized


# ---------------------------------------------------------------------------
# Module-level convenience
# ---------------------------------------------------------------------------

_default_adapter: Optional[RandomnessAdapter] = None

def _get_default() -> RandomnessAdapter:
    global _default_adapter
    if _default_adapter is None:
        _default_adapter = RandomnessAdapter()
    return _default_adapter

def get_beacon(round_id: Optional[int] = None, *, adapter: Optional[RandomnessAdapter] = None) -> Dict[str, Any]:
    return (adapter or _get_default()).get_beacon(round_id)

def get_round(*, adapter: Optional[RandomnessAdapter] = None) -> Dict[str, Any]:
    return (adapter or _get_default()).get_round()

def get_history(limit: int = 10, before_round: Optional[int] = None, *, adapter: Optional[RandomnessAdapter] = None) -> Dict[str, Any]:
    return (adapter or _get_default()).get_history(limit=limit, before_round=before_round)

def beacon_output_bytes(beacon: Dict[str, Any]) -> bytes:
    """
    Decode the 'output' field of a normalized beacon.
    """
    return _hex_to_bytes(beacon["output"])

def latest_output_bytes(*, adapter: Optional[RandomnessAdapter] = None) -> bytes:
    """
    Fetch the latest beacon and return its output as raw bytes.
    """
    b = get_beacon(adapter=adapter)
    return beacon_output_bytes(b)
