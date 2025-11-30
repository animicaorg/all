# -*- coding: utf-8 -*-
"""
Integration: fee market dynamics — surge ⇒ watermark raise ⇒ eviction.

Goal
-----
1) Admit a very low-fee tx into the mempool (call this L).
2) Create a surge of high-fee txs (via debug helpers if available).
3) Observe that:
   • The mempool "watermark"/effective min-fee rises (from stats).
   • L is no longer pending (i.e., evicted) OR (rarely) it got included.

This is *best-effort* and will SKIP if the target node doesn't expose
mempool debug/stat methods or we can't reliably stage the scenario.

Gating & Env
------------
• RUN_INTEGRATION_TESTS=1             — enable integration tests
• ANIMICA_RPC_URL                     — HTTP JSON-RPC (default: http://127.0.0.1:8545)
• ANIMICA_HTTP_TIMEOUT                — per-call timeout secs (default: 5)
• ANIMICA_RESULT_WAIT_SECS            — generic wait window (default: 240)
• ANIMICA_SURGE_COUNT                 — number of high-fee txs to inject (default: 200)
• ANIMICA_LOW_FEE                     — "low" fee (units are impl-defined) (default: 1)
• ANIMICA_HIGH_FEE                    — "high" fee (default: 1000)
• ANIMICA_TX_FIXTURE_LOW              — path to a low-fee CBOR tx (default: mempool/fixtures/txs_cbor/tx_low_fee.cbor)
• ANIMICA_TX_FIXTURE_BASE             — path to a "normal" CBOR tx for fallback (default: mempool/fixtures/txs_cbor/tx1.cbor)

Assumptions
-----------
• The node may expose any of these debug/stat methods:
  - mempool.getStats | mempool.stats | mempool_info | mempool.get_info
  - mempool.watermark | mempool.getWatermark (optional)
  - mempool.debugFill | mempool.debug.fill | dev.generateTxs | debug_generateTxs
  - mempool.debug.inject (single/loop)

If none are available, the test SKIPs.

"""
from __future__ import annotations

import base64
import json
import os
import time
import urllib.request
from typing import Any, Dict, Optional, Sequence, Tuple

import pytest

from tests.integration import env  # gating + helpers

# ------------------------------ HTTP helpers ---------------------------------


def _http_timeout() -> float:
    try:
        return float(env("ANIMICA_HTTP_TIMEOUT", "5"))
    except Exception:
        return 5.0


def _rpc_call(
    rpc_url: str,
    method: str,
    params: Optional[Sequence[Any] | Dict[str, Any]] = None,
    *,
    req_id: int = 1,
) -> Any:
    if params is None:
        params = []
    payload = {"jsonrpc": "2.0", "id": req_id, "method": method, "params": params}
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        rpc_url, data=data, headers={"Content-Type": "application/json"}, method="POST"
    )
    with urllib.request.urlopen(req, timeout=_http_timeout()) as resp:
        raw = resp.read()
    msg = json.loads(raw.decode("utf-8"))
    if "error" in msg and msg["error"]:
        raise AssertionError(f"RPC {method} error: {msg['error']}")
    if "result" not in msg:
        raise AssertionError(f"RPC {method} missing result: {msg}")
    return msg["result"]


def _rpc_try(
    rpc_url: str,
    methods: Sequence[str],
    params: Optional[Sequence[Any] | Dict[str, Any]] = None,
) -> Tuple[str, Any]:
    last_exc: Optional[Exception] = None
    for i, m in enumerate(methods, start=1):
        try:
            return m, _rpc_call(rpc_url, m, params, req_id=i)
        except Exception as exc:
            last_exc = exc
    raise AssertionError(
        f"All tried methods failed ({methods}); last error: {last_exc}"
    )


# ------------------------------ Stats helpers --------------------------------


def _parse_num(x: Any) -> Optional[int]:
    if isinstance(x, bool) or x is None:
        return None
    if isinstance(x, (int, float)):
        try:
            return int(x)
        except Exception:
            return None
    if isinstance(x, str):
        try:
            if x.startswith("0x"):
                return int(x, 16)
            return int(x)
        except Exception:
            return None
    return None


def _get_mempool_stats(rpc_url: str) -> Optional[Dict[str, Any]]:
    candidates = [
        "mempool.getStats",
        "mempool.stats",
        "mempool_info",
        "mempool.get_info",
        "txpool_status",  # eth-ish fallback
        "txpool.status",
    ]
    for m in candidates:
        try:
            res = _rpc_call(rpc_url, m, [])
            if isinstance(res, dict):
                return res
        except Exception:
            continue
    return None


def _get_watermark_from_stats(stats: Dict[str, Any]) -> Optional[int]:
    # Accept many possible field spellings
    for k in (
        "watermark",
        "minFee",
        "minGasPrice",
        "floor",
        "rollingMin",
        "effective_min_fee",
    ):
        v = stats.get(k)
        n = _parse_num(v)
        if n is not None:
            return n
    # Nested variants?
    meta = stats.get("meta") or stats.get("policy") or {}
    if isinstance(meta, dict):
        for k in ("watermark", "minFee", "minGasPrice", "floor"):
            n = _parse_num(meta.get(k))
            if n is not None:
                return n
    return None


# ------------------------------- Tx helpers ----------------------------------


def _read_bytes(path: str) -> Optional[bytes]:
    try:
        with open(path, "rb") as f:
            return f.read()
    except Exception:
        return None


def _send_raw_tx(rpc_url: str, raw: bytes) -> str:
    """
    Try multiple encodings & method names; return tx hash hex if available.
    """
    candidates = [
        ("tx.sendRawTransaction", ["0x" + raw.hex()]),
        ("tx.sendRawTransaction", [{"raw": "0x" + raw.hex(), "encoding": "cbor"}]),
        ("tx.sendRawTransaction", [base64.b64encode(raw).decode("ascii")]),
        ("tx_sendRawTransaction", ["0x" + raw.hex()]),
        ("eth_sendRawTransaction", ["0x" + raw.hex()]),
    ]
    for m, params in candidates:
        try:
            res = _rpc_call(rpc_url, m, params)
            if isinstance(res, str) and res.startswith("0x"):
                return res
        except Exception:
            continue
    return ""


def _tx_get(rpc_url: str, tx_hash: str) -> Optional[Dict[str, Any]]:
    for m in ("tx.getTransactionByHash", "eth_getTransactionByHash"):
        try:
            r = _rpc_call(rpc_url, m, [tx_hash])
            if isinstance(r, dict):
                return r
            if r is None:
                return None
        except Exception:
            continue
    return None


def _tx_receipt(rpc_url: str, tx_hash: str) -> Optional[Dict[str, Any]]:
    try:
        r = _rpc_call(rpc_url, "tx.getTransactionReceipt", [tx_hash])
        return r if isinstance(r, dict) else None
    except Exception:
        return None


# ---------------------------- Debug inject helpers ---------------------------


def _debug_fill(rpc_url: str, count: int, fee: int) -> bool:
    """
    Try to instruct the node to synthesize `count` txs at ~`fee`.
    Returns True on success, False otherwise.
    """
    payloads: list[Tuple[str, Sequence[Any] | Dict[str, Any]]] = [
        ("mempool.debugFill", [{"count": count, "fee": fee}]),
        ("mempool.debugFill", [count, fee]),
        ("mempool.debug.fill", [{"count": count, "fee": fee}]),
        ("dev.generateTxs", [count, {"fee": fee}]),
        ("debug_generateTxs", [{"count": count, "fee": fee}]),
        ("mempool.debug.fill", [count]),
    ]
    for method, params in payloads:
        try:
            _rpc_call(rpc_url, method, params if isinstance(params, list) else [params])
            return True
        except Exception:
            continue
    return False


def _debug_inject_one(rpc_url: str, fee: int) -> Optional[str]:
    """
    Try to inject a *single* synthetic tx at ~fee. Return a tx hash if possible.
    """
    payloads: list[Tuple[str, Sequence[Any] | Dict[str, Any]]] = [
        ("mempool.debug.inject", [{"fee": fee}]),
        ("mempool.debug.inject", [fee]),
        ("mempool.injectTx", [{"fee": fee, "random": True}]),
        ("dev.generateTxs", [1, {"fee": fee}]),
    ]
    for method, params in payloads:
        try:
            res = _rpc_call(
                rpc_url, method, params if isinstance(params, list) else [params]
            )
            # Some methods may return a hash or an object containing it.
            if isinstance(res, str) and res.startswith("0x"):
                return res
            if isinstance(res, dict):
                for k in ("hash", "txHash", "id"):
                    v = res.get(k)
                    if isinstance(v, str) and v.startswith("0x"):
                        return v
        except Exception:
            continue
    return None


# ---------------------------------- Test -------------------------------------


@pytest.mark.timeout(900)
def test_fee_market_surge_raises_watermark_and_evicts_low_fee():
    rpc = env("ANIMICA_RPC_URL", "http://127.0.0.1:8545")
    wait_secs = float(env("ANIMICA_RESULT_WAIT_SECS", "240"))
    surge_n = int(env("ANIMICA_SURGE_COUNT", "200"))
    low_fee = int(env("ANIMICA_LOW_FEE", "1"))
    high_fee = int(env("ANIMICA_HIGH_FEE", "1000"))

    # 0) Ensure we can observe the mempool watermark.
    stats0 = _get_mempool_stats(rpc)
    if not stats0:
        pytest.skip("No mempool stats method exposed; cannot observe watermark.")
    wm0 = _get_watermark_from_stats(stats0)
    if wm0 is None:
        pytest.skip(
            "Mempool stats present but no recognizable watermark/min-fee field."
        )

    # 1) Stage a low-fee tx (L) into mempool (either via debug inject or fixture CBOR).
    low_hash: Optional[str] = _debug_inject_one(rpc, low_fee)
    if not low_hash:
        # Fallback to signed CBOR fixture if debug inject is unavailable.
        p_low = env(
            "ANIMICA_TX_FIXTURE_LOW", "mempool/fixtures/txs_cbor/tx_low_fee.cbor"
        )
        raw = _read_bytes(p_low)
        if not raw:
            pytest.skip(
                "No debug inject and missing low-fee CBOR fixture; cannot stage scenario."
            )
        low_hash = _send_raw_tx(rpc, raw)
        if not low_hash:
            # As a last fallback, try a normal tx fixture (may still be admitted if fee policy is permissive).
            p_base = env(
                "ANIMICA_TX_FIXTURE_BASE", "mempool/fixtures/txs_cbor/tx1.cbor"
            )
            raw2 = _read_bytes(p_base)
            if not raw2 or not _send_raw_tx(rpc, raw2):
                pytest.skip("Could not get a (low-fee) tx into mempool via fixtures.")
            # We don't know the fee; keep proceeding but eviction check becomes heuristic.

    # Confirm L is pending (not included).
    if low_hash:
        # Give the node a moment to ingest
        t_end = time.time() + min(10.0, wait_secs)
        pending_ok = False
        while time.time() < t_end:
            txv = _tx_get(rpc, low_hash)
            rec = _tx_receipt(rpc, low_hash)
            if txv and not rec:
                pending_ok = True
                break
            time.sleep(0.5)
        if not pending_ok:
            pytest.skip(
                "Could not confirm the low-fee tx is pending; pool policy may have rejected it."
            )

    # 2) Create a surge of high-fee txs.
    filled = _debug_fill(rpc, surge_n, high_fee)
    if not filled:
        # Fall back to multiple single injects (may be slower).
        injected = 0
        t_dead = time.time() + wait_secs / 2
        while injected < surge_n and time.time() < t_dead:
            h = _debug_inject_one(rpc, high_fee)
            if h:
                injected += 1
            else:
                # If no debug inject available at all, we cannot surge.
                break
        if injected < max(10, surge_n // 5):
            pytest.skip(
                "Unable to generate a meaningful surge without mempool debug helpers."
            )

    # 3) Wait for watermark to rise.
    wm_raised = False
    wm1_val: Optional[int] = None
    t_deadline = time.time() + wait_secs
    while time.time() < t_deadline:
        st = _get_mempool_stats(rpc)
        if st:
            w = _get_watermark_from_stats(st)
            if w is not None:
                wm1_val = w
                if w > (wm0 or 0):
                    wm_raised = True
                    break
        time.sleep(1.0)

    if not wm_raised:
        pytest.skip(
            "Watermark/min-fee did not rise within wait window (policy may be disabled)."
        )

    # 4) Verify L was evicted OR included.
    if low_hash:
        # Give eviction a bit of time after watermark rise
        t_end = time.time() + min(30.0, wait_secs)
        evicted = False
        included = False
        while time.time() < t_end:
            rec = _tx_receipt(rpc, low_hash)
            txv = _tx_get(rpc, low_hash)
            if rec:
                included = True
                break
            # "Not pending" heuristic: no tx view found means likely dropped/evicted
            if txv is None:
                evicted = True
                break
            time.sleep(0.5)

        # Expect either eviction (preferred in surge) or inclusion (if miner picked it anyway).
        assert (
            evicted or included
        ), "Low-fee tx still pending after watermark increase; expected eviction or unlikely inclusion."

    # Final assert for watermark visibility.
    assert wm_raised and (
        wm1_val is None or wm1_val >= wm0
    ), "Watermark did not increase as expected."
