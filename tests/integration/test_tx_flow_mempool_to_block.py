# -*- coding: utf-8 -*-
"""
Integration: submit → pooled → included

This black-box test submits a pre-signed CBOR transaction to a running node via
JSON-RPC, observes it in the pending pool (when exposed), and waits for it to
be included in a block with a successful receipt.

Enable package-wide with:
    RUN_INTEGRATION_TESTS=1

Environment (optional unless noted):
    ANIMICA_RPC_URL                 JSON-RPC URL (default: http://127.0.0.1:8545)
    ANIMICA_HTTP_TIMEOUT            RPC call timeout seconds (default: 5)

    ANIMICA_TX_CBOR_PATH            REQUIRED unless a default fixture exists locally.
                                    Path to a *signed* CBOR tx file (binary).
                                    Fallback search order (relative to repo root):
                                      - mempool/fixtures/txs_cbor/tx1.cbor
                                      - execution/fixtures/tx_transfer_valid.cbor

    ANIMICA_PENDING_WAIT            Seconds to look for the tx in pending pool (default: 20)
    ANIMICA_INCLUDE_TIMEOUT         Seconds to wait for inclusion in a block (default: 120)
    ANIMICA_POLL_INTERVAL           Poll cadence seconds for both phases (default: 1.0)

Notes:
  * We don't build/sign the tx here. Provide a devnet-signed transfer that is
    valid for the running node's chainId and funded sender.
  * Pending visibility depends on the node's RPC surface. We try:
        - tx.getTransactionByHash (expects to return an object; may include "pending": true)
        - tx.getTransaction (alt spelling)
    If pending is never observable but the tx gets included quickly, we still pass.
"""
from __future__ import annotations

import json
import os
import time
import urllib.request
from pathlib import Path
from typing import Any, Dict, Optional, Sequence, Tuple

import pytest

from tests.integration import \
    env  # RUN_INTEGRATION_TESTS gate in package __init__

# -------------------------------- RPC helpers --------------------------------


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
    if isinstance(params, dict):
        payload = {"jsonrpc": "2.0", "id": req_id, "method": method, "params": params}
    else:
        payload = {
            "jsonrpc": "2.0",
            "id": req_id,
            "method": method,
            "params": list(params),
        }
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        rpc_url,
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=_http_timeout()) as resp:
        raw = resp.read()
    msg = json.loads(raw.decode("utf-8"))
    if "error" in msg and msg["error"]:
        raise AssertionError(f"JSON-RPC error from {method}: {msg['error']}")
    if "result" not in msg:
        raise AssertionError(f"JSON-RPC response missing 'result' for {method}: {msg}")
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
            continue
    raise AssertionError(
        f"All RPC spellings failed ({methods}). Last error: {last_exc}"
    )


# --------------------------------- Helpers -----------------------------------


def _as_hex(b: bytes) -> str:
    return "0x" + b.hex()


def _load_signed_cbor_bytes() -> bytes:
    # Priority: explicit path via env
    env_path = env("ANIMICA_TX_CBOR_PATH")
    candidates = []
    if env_path:
        candidates.append(Path(env_path))
    # Common repo fixture fallbacks
    candidates.append(Path("mempool/fixtures/txs_cbor/tx1.cbor"))
    candidates.append(Path("execution/fixtures/tx_transfer_valid.cbor"))

    for p in candidates:
        if p.is_file():
            return p.read_bytes()

    msg = (
        "No signed CBOR tx found. Set ANIMICA_TX_CBOR_PATH to a valid file, "
        "or ensure a fixture exists at mempool/fixtures/txs_cbor/tx1.cbor "
        "or execution/fixtures/tx_transfer_valid.cbor"
    )
    pytest.skip(msg)
    raise AssertionError("unreachable")


def _is_hex_hash(val: Any) -> bool:
    return isinstance(val, str) and val.startswith("0x") and len(val) >= 10


def _extract_tx_hash(send_result: Any) -> str:
    """
    Accepts either:
      - "0x…" (string tx hash), or
      - {"txHash": "0x…"} / {"hash": "0x…"}
    """
    if _is_hex_hash(send_result):
        return send_result  # type: ignore[return-value]
    if isinstance(send_result, dict):
        for k in ("txHash", "hash", "transactionHash"):
            v = send_result.get(k)
            if _is_hex_hash(v):
                return v  # type: ignore[return-value]
    raise AssertionError(
        f"Unrecognized sendRawTransaction result shape: {send_result!r}"
    )


def _tx_get(rpc_url: str, tx_hash: str) -> Optional[Dict[str, Any]]:
    for methods in (
        ("tx.getTransactionByHash",),
        ("tx.getTransaction",),
        ("getTransactionByHash",),
    ):
        try:
            _, res = _rpc_try(rpc_url, methods, [tx_hash])
            if res is None:
                return None
            if isinstance(res, dict):
                return res
        except Exception:
            continue
    return None


def _receipt_get(rpc_url: str, tx_hash: str) -> Optional[Dict[str, Any]]:
    for methods in (
        ("tx.getTransactionReceipt",),
        ("getTransactionReceipt",),
        ("eth_getTransactionReceipt",),
    ):
        try:
            _, res = _rpc_try(rpc_url, methods, [tx_hash])
            if res is None:
                return None
            if isinstance(res, dict):
                return res
        except Exception:
            continue
    return None


def _is_success_receipt(rcpt: Dict[str, Any]) -> bool:
    # Accept a variety of encodings
    status = rcpt.get("status")
    if status is True:
        return True
    if isinstance(status, int):
        return status != 0
    if isinstance(status, str):
        s = status.strip().lower()
        if s in ("success", "ok"):
            return True
        if s.startswith("0x"):
            try:
                return int(s, 16) != 0
            except Exception:
                pass
        try:
            return int(s, 10) != 0
        except Exception:
            pass
    # Some receipts omit status but include gasUsed/logs; treat as success iff blockHash present and no explicit failure marker.
    return bool(rcpt.get("blockHash"))


# ----------------------------------- Test ------------------------------------


@pytest.mark.timeout(240)
def test_submit_pooled_then_included_successfully():
    rpc_url = env("ANIMICA_RPC_URL", "http://127.0.0.1:8545")
    assert rpc_url, "ANIMICA_RPC_URL must be set to a non-empty URL"

    cbor_bytes = _load_signed_cbor_bytes()
    raw_hex = _as_hex(cbor_bytes)

    # 1) Submit the raw tx (hex-encoded CBOR)
    send_method, send_res = _rpc_try(
        rpc_url,
        methods=(
            "tx.sendRawTransaction",
            "sendRawTransaction",
            "eth_sendRawTransaction",
        ),
        params=[raw_hex],
    )
    tx_hash = _extract_tx_hash(send_res)
    assert _is_hex_hash(tx_hash), f"{send_method} returned non-hash: {send_res!r}"

    # Timing knobs
    pending_wait = float(env("ANIMICA_PENDING_WAIT", "20") or "20")
    include_timeout = float(env("ANIMICA_INCLUDE_TIMEOUT", "120") or "120")
    poll = float(env("ANIMICA_POLL_INTERVAL", "1.0") or "1.0")

    # 2) Try to observe in the pending pool (best-effort)
    pooled_seen = False
    t0 = time.time()
    while time.time() - t0 < pending_wait:
        tx = _tx_get(rpc_url, tx_hash)
        if isinstance(tx, dict):
            # Heuristics: "pending": True, or no blockHash yet
            if bool(tx.get("pending")) or not tx.get("blockHash"):
                pooled_seen = True
                break
        time.sleep(poll)

    # 3) Wait for inclusion & success receipt
    deadline = time.time() + include_timeout
    receipt: Optional[Dict[str, Any]] = None
    while time.time() < deadline:
        rcpt = _receipt_get(rpc_url, tx_hash)
        if isinstance(rcpt, dict) and rcpt.get("blockHash"):
            receipt = rcpt
            break
        time.sleep(poll)

    assert (
        receipt is not None
    ), f"Transaction {tx_hash} was not included within {include_timeout:.1f}s"

    assert _is_success_receipt(
        receipt
    ), f"Transaction {tx_hash} included but not successful: {receipt!r}"

    # 4) If we never saw 'pending', allow fast-inclusion exception:
    #     If the tx was included in < 1 poll interval after submit, it's plausible
    #     we missed the mempool window; otherwise, require pending visibility.
    if not pooled_seen:
        # Rough wall clock from submit to inclusion
        submit_to_include = max(0.0, time.time() - t0)
        if submit_to_include > (2 * poll):
            pytest.fail(
                "Tx was never observed in pending pool before inclusion; "
                f"submit→include took {submit_to_include:.2f}s which exceeds the "
                f"fast inclusion allowance (2*poll={2*poll:.2f}s). "
                "Ensure tx.getTransactionByHash exposes pending entries."
            )
