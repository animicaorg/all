# -*- coding: utf-8 -*-
"""
Gate (integration): a devnet is reachable and the head advances.

Requirements to run:
  - Set RUN_INTEGRATION_TESTS=1 (package-level gate in tests/integration/__init__.py)
  - Optionally set:
      ANIMICA_RPC_URL              (default: http://127.0.0.1:8545)
      ANIMICA_CHAIN_ID             (optional expected chain id, e.g. "1" or "animica:1")
      ANIMICA_HTTP_TIMEOUT         (seconds, default 5)
      ANIMICA_HEAD_ADVANCE_TIMEOUT (seconds, default 60)
      ANIMICA_HEAD_POLL_INTERVAL   (seconds, default 1.0)

What this test does:
  1) Calls chain.getParams and sanity-checks the returned shape; if ANIMICA_CHAIN_ID
     is provided, it must match (supports plain int, "0x1", or "animica:1").
  2) Polls chain.getHead until the height increases from the initial value within
     the configured timeout.

The JSON-RPC method names follow spec/openrpc.json. We also try a few fallback
spellings to be future-proof.
"""
from __future__ import annotations

import json
import time
import urllib.request
from typing import Any, Dict, Optional, Sequence, Tuple, Union

import pytest

from tests.integration import env, require_env


# ------------------------------- RPC helpers ---------------------------------

def _http_timeout() -> float:
    try:
        return float(env("ANIMICA_HTTP_TIMEOUT", "5"))
    except Exception:
        return 5.0


def _rpc_call(rpc_url: str, method: str, params: Optional[Union[Dict[str, Any], Sequence[Any]]] = None, *, req_id: int = 1) -> Any:
    if params is None:
        params = []
    if isinstance(params, dict):
        # JSON-RPC 2.0 supports named params; keep as-is
        payload = {"jsonrpc": "2.0", "id": req_id, "method": method, "params": params}
    else:
        payload = {"jsonrpc": "2.0", "id": req_id, "method": method, "params": list(params)}

    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        rpc_url,
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=_http_timeout()) as resp:
        body = resp.read()
    msg = json.loads(body.decode("utf-8"))

    if "error" in msg and msg["error"]:
        raise AssertionError(f"JSON-RPC error from {method}: {msg['error']}")
    if "result" not in msg:
        raise AssertionError(f"JSON-RPC response missing 'result' for {method}: {msg}")
    return msg["result"]


def _rpc_try(rpc_url: str, methods: Sequence[str], params: Optional[Union[Dict[str, Any], Sequence[Any]]] = None) -> Tuple[str, Any]:
    last_exc: Optional[Exception] = None
    for i, m in enumerate(methods, start=1):
        try:
            return m, _rpc_call(rpc_url, m, params, req_id=i)
        except Exception as exc:  # try next spelling
            last_exc = exc
            continue
    raise AssertionError(f"All RPC method spellings failed ({methods}). Last error: {last_exc}")


# ------------------------------- Parsing utils -------------------------------

def _parse_chain_id(result: Any) -> Optional[int]:
    """
    Accept a variety of shapes:
      - {"chainId": 1}
      - {"chain": {"id": 1}} or {"params": {"chainId": 1}}
      - {"chainId": "0x1"} or {"chainId": "animica:1"} (CAIP-2-like)
    """
    # dig helpers
    def dig(d: Dict[str, Any], *keys: str) -> Optional[Any]:
        cur: Any = d
        for k in keys:
            if not isinstance(cur, dict) or k not in cur:
                return None
            cur = cur[k]
        return cur

    value: Any = None
    if isinstance(result, dict):
        value = (
            result.get("chainId")
            or dig(result, "chain", "id")
            or dig(result, "params", "chainId")
            or dig(result, "consensus", "chainId")
        )

    if value is None:
        return None

    # normalize
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        s = value.strip().lower()
        if s.startswith("0x"):
            try:
                return int(s, 16)
            except Exception:
                return None
        if ":" in s:
            # CAIP-2 like: "animica:1"
            try:
                return int(s.split(":", 1)[1], 10)
            except Exception:
                return None
        try:
            return int(s, 10)
        except Exception:
            return None
    return None


def _parse_height(head: Any) -> int:
    """
    Accept head objects with any of:
      - {"height": 123}
      - {"number": 123}
      - {"index": 123}
    """
    if isinstance(head, dict):
        for k in ("height", "number", "index"):
            v = head.get(k)
            if isinstance(v, int):
                return v
            if isinstance(v, str):
                try:
                    return int(v, 0)
                except Exception:
                    pass
    raise AssertionError(f"Unrecognized head shape (no height/number/index): {head!r}")


# ----------------------------------- Tests -----------------------------------

@pytest.mark.timeout(20)
def test_chain_params_and_id():
    rpc_url = env("ANIMICA_RPC_URL", "http://127.0.0.1:8545")
    assert rpc_url, "ANIMICA_RPC_URL must resolve to a non-empty string"

    method, params = _rpc_try(
        rpc_url,
        methods=("chain.getParams", "chain.get_parameters", "chain.params", "getParams"),
        params=[],
    )
    # Minimal sanity: it's a dict with at least one known subkey or non-empty.
    assert isinstance(params, dict) and len(params) > 0, f"{method} returned unexpected shape: {params!r}"

    expected = env("ANIMICA_CHAIN_ID")
    if expected is not None:
        # normalize expected
        exp_norm = _parse_chain_id({"chainId": expected})
        assert exp_norm is not None, f"Could not parse expected ANIMICA_CHAIN_ID={expected!r}"
        got = _parse_chain_id(params)
        assert got == exp_norm, f"chainId mismatch: got {got}, expected {exp_norm}"
    else:
        # If no expectation provided, at least ensure we can parse a positive id.
        got = _parse_chain_id(params)
        assert got is None or got > 0, f"chainId should be positive if present, got {got!r}"


@pytest.mark.timeout(90)
def test_head_advances_within_timeout():
    rpc_url = env("ANIMICA_RPC_URL", "http://127.0.0.1:8545")
    assert rpc_url, "ANIMICA_RPC_URL must resolve to a non-empty string"

    _, head0 = _rpc_try(
        rpc_url,
        methods=("chain.getHead", "chain.head", "getHead"),
        params=[],
    )
    h0 = _parse_height(head0)

    # Poll for advancement
    timeout_s = float(env("ANIMICA_HEAD_ADVANCE_TIMEOUT", "60") or "60")
    interval_s = float(env("ANIMICA_HEAD_POLL_INTERVAL", "1.0") or "1.0")
    deadline = time.time() + timeout_s

    last_height = h0
    while time.time() < deadline:
        time.sleep(interval_s)
        _, head = _rpc_try(rpc_url, methods=("chain.getHead", "chain.head", "getHead"), params=[])
        h = _parse_height(head)
        # allow monotonic non-decreasing; break when strictly greater
        if h > last_height:
            return
        last_height = h

    pytest.fail(
        f"Head did not advance within {timeout_s:.1f}s "
        f"(initial height {h0}, last observed {last_height})"
    )
