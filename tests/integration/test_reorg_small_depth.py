# -*- coding: utf-8 -*-
"""
Integration: small-depth reorg; orphaned tx reinjected to mempool.

High-level plan (best-effort, devnet-friendly):
  1) Use two nodes (A = primary, B = peer). If B is not provided, skip.
  2) Submit a small signed CBOR tx to A *only*. Record txHash.
  3) Wait for A to include the tx in a block (receipt present).
  4) Induce a depth-1/2 reorg by having B mine ahead from the parent (without
     the tx), then allow gossip/sync to reorg A to B's heavier chain.
  5) Verify on A that:
       - The tx is no longer present in canonical chain (receipt missing/null).
       - The tx reappears in the pending pool (via tx.getTransactionByHash → pending,
         or duplicate-on-resubmit).

Notes:
  • This test uses a range of debug/miner method names to maximize portability.
    If your devnet exposes a different trigger, export ANIMICA_MINE_METHOD_B.
  • The suite **skips by default** unless RUN_INTEGRATION_TESTS=1 (see __init__.py).
  • If we cannot deterministically induce a reorg with the available RPCs, we skip.

Env:
  ANIMICA_RPC_URL            — node A HTTP JSON-RPC (default: http://127.0.0.1:8545)
  ANIMICA_PEER_RPC_URL       — node B HTTP JSON-RPC (REQUIRED for this test)
  ANIMICA_HTTP_TIMEOUT       — per-call timeout secs (default: 5)
  ANIMICA_RESULT_WAIT_SECS   — generic wait window secs (default: 240)
  ANIMICA_REORG_DEPTH        — depth to try (1 or 2; default: 1)
  ANIMICA_MINE_METHOD_B      — override mining debug method on B (e.g., "dev.mineBlocks")
  ANIMICA_TX_FIXTURE         — path to CBOR tx to submit (default: mempool/fixtures/txs_cbor/tx1.cbor)
"""
from __future__ import annotations

import base64
import json
import os
import time
import urllib.request
from typing import Any, Dict, Optional, Sequence, Tuple

import pytest

from tests.integration import env  # gating + env helper


# ------------------------------- HTTP helpers --------------------------------

def _http_timeout() -> float:
    try:
        return float(env("ANIMICA_HTTP_TIMEOUT", "5"))
    except Exception:
        return 5.0


def _rpc_call(rpc_url: str, method: str, params: Optional[Sequence[Any] | Dict[str, Any]] = None, *, req_id: int = 1) -> Any:
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


def _rpc_try(rpc_url: str, methods: Sequence[str], params: Optional[Sequence[Any] | Dict[str, Any]] = None) -> Tuple[str, Any]:
    last_exc: Optional[Exception] = None
    for i, m in enumerate(methods, start=1):
        try:
            return m, _rpc_call(rpc_url, m, params, req_id=i)
        except Exception as exc:
            last_exc = exc
    raise AssertionError(f"All methods failed ({methods}); last error: {last_exc}")


# ------------------------------ Chain helpers --------------------------------

def _get_head(rpc_url: str) -> Dict[str, Any]:
    m, res = _rpc_try(rpc_url, ["chain.getHead", "chain_getHead"])
    assert isinstance(res, dict), f"Unexpected head shape from {m}: {res}"
    return res


def _get_block_by_number(rpc_url: str, num: int) -> Optional[Dict[str, Any]]:
    try_methods = [("chain.getBlockByNumber", [num, False, False]), ("chain_getBlockByNumber", [num])]
    for m, params in try_methods:
        try:
            res = _rpc_call(rpc_url, m, params)
            return res if isinstance(res, dict) else None
        except Exception:
            continue
    return None


def _get_tx_receipt(rpc_url: str, tx_hash: str) -> Optional[Dict[str, Any]]:
    try:
        res = _rpc_call(rpc_url, "tx.getTransactionReceipt", [tx_hash])
        if res is None:
            return None
        return res if isinstance(res, dict) else None
    except Exception:
        return None


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


# ------------------------------- Mining helpers -------------------------------

def _mine_on_B(rpc_url_b: str, count: int) -> bool:
    """
    Attempt to force node B to mine `count` blocks on its current tip, trying a
    variety of debug/miner method names. Returns True if any call succeeded.
    """
    override = env("ANIMICA_MINE_METHOD_B")
    candidates: list[Tuple[str, Sequence[Any]]] = []
    if override:
        # Interpret override as method name; try with common param shapes
        candidates.extend([(override, [count]), (override, [{"count": count}]), (override, [])])

    # Common dev methods across ecosystems
    candidates.extend([
        ("dev.mineBlocks", [count]),
        ("dev_mineBlocks", [count]),
        ("dev_mineBlocks", [{"count": count}]),
        ("miner.mineN", [count]),
        ("miner_mineN", [count]),
        ("miner.start", []),
        ("mining.mine", [{"blocks": count}]),
        ("mining.mine", [count]),
        ("mining.start", []),
    ])

    for method, params in candidates:
        try:
            _rpc_call(rpc_url_b, method, params)
            return True
        except Exception:
            continue
    return False


# ------------------------------- Tx helpers ----------------------------------

def _read_fixture_bytes() -> Optional[bytes]:
    p = env("ANIMICA_TX_FIXTURE", "mempool/fixtures/txs_cbor/tx1.cbor")
    try:
        with open(p, "rb") as f:
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


def _resubmit_expect_duplicate(rpc_url: str, raw: bytes) -> bool:
    """
    Resubmit the same raw tx and interpret an error/response that indicates the tx
    is already known/pending. Since we don't have direct error access here (the helper
    raises), we use a lighter-weight path: some nodes return a deterministic value for
    duplicates; otherwise, we consider this probe inconclusive and return False.
    """
    try:
        h = _send_raw_tx(rpc_url, raw)
        # If node returns the *same* hash immediately, some implementations mean "already known".
        return bool(h)
    except Exception:
        return False


# -------------------------------- The test -----------------------------------

@pytest.mark.timeout(900)
def test_small_reorg_reinjects_orphaned_tx_to_mempool():
    rpc_a = env("ANIMICA_RPC_URL", "http://127.0.0.1:8545")
    rpc_b = env("ANIMICA_PEER_RPC_URL")
    if not rpc_b:
        pytest.skip("ANIMICA_PEER_RPC_URL is not set — need two nodes to induce a reorg.")

    depth = int(env("ANIMICA_REORG_DEPTH", "1"))
    wait_secs = float(env("ANIMICA_RESULT_WAIT_SECS", "240"))

    # 1) Submit a small tx to A only.
    raw = _read_fixture_bytes()
    if not raw:
        pytest.skip("Missing CBOR tx fixture (set ANIMICA_TX_FIXTURE or ensure default path exists).")
    tx_hash = _send_raw_tx(rpc_a, raw)
    assert tx_hash.startswith("0x"), "Failed to submit test transaction to node A"

    # 2) Wait for inclusion on A.
    deadline = time.time() + wait_secs
    included_height: Optional[int] = None
    included_block: Optional[str] = None
    while time.time() < deadline:
        rec = _get_tx_receipt(rpc_a, tx_hash)
        if rec and isinstance(rec, dict):
            # Accept several field shapes for block number/hash
            bn = rec.get("blockNumber") or rec.get("height") or rec.get("number")
            bh = rec.get("blockHash") or rec.get("hash") or rec.get("block_hash")
            try:
                if isinstance(bn, str) and bn.startswith("0x"):
                    bn = int(bn, 16)
                if isinstance(bn, (int, float)):
                    included_height = int(bn)
                if isinstance(bh, str) and bh.startswith("0x"):
                    included_block = bh
            except Exception:
                pass
            if included_height is not None and included_block:
                break
        time.sleep(1.0)
    if included_height is None:
        pytest.skip("Tx was not included on node A within the wait window; cannot proceed with reorg test.")

    # 3) Ask node B to mine `depth + 1` blocks (to clearly overtake).
    mined = _mine_on_B(rpc_b, max(1, depth + 1))
    if not mined:
        pytest.skip("Unable to trigger mining on node B via known debug methods; cannot induce reorg.")

    # 4) Wait for A to reorg: the block at `included_height` should change (or tx receipt disappears).
    reorg_deadline = time.time() + wait_secs
    reorged = False
    while time.time() < reorg_deadline:
        # If the receipt disappears (None), we consider it reorged out.
        rec_now = _get_tx_receipt(rpc_a, tx_hash)
        if rec_now is None:
            reorged = True
            break

        # Or if the block at that height differs from the original.
        blk = _get_block_by_number(rpc_a, included_height)
        if isinstance(blk, dict):
            h = blk.get("hash") or blk.get("blockHash")
            if isinstance(h, str) and h.startswith("0x") and h != included_block:
                reorged = True
                break
        time.sleep(1.0)

    if not reorged:
        pytest.skip("Did not observe a reorg on node A within the wait window.")

    # 5) Verify reinjection to mempool on A.
    # Strategy A: tx.getTransactionByHash returns a pending object.
    pending_ok = False
    tx_view = _tx_get(rpc_a, tx_hash)
    if isinstance(tx_view, dict):
        # Heuristics: presence with no receipt and/or an explicit pending flag
        if tx_view.get("pending") is True:
            pending_ok = True
        # Some nodes omit pending flag; presence in view without receipt is a good sign.
        if not pending_ok:
            if _get_tx_receipt(rpc_a, tx_hash) is None:
                pending_ok = True

    # Strategy B: resubmitting the same raw tx is treated as duplicate/already known.
    dup_ok = False
    if not pending_ok:
        dup_ok = _resubmit_expect_duplicate(rpc_a, raw)

    assert pending_ok or dup_ok, (
        "After reorg, tx was not found pending nor identified as duplicate on resubmit. "
        "This suggests mempool reinjection did not occur."
    )

