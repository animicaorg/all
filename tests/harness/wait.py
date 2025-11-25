"""
Wait utilities for tests
========================

Convenience helpers to wait for:
  • new heads (via WS if available, else HTTP polling)
  • a target chain height
  • a transaction receipt

These are tolerant to slight RPC method/name differences across nodes.
They prefer Omni-style methods but gracefully fall back to Ethereum-compat.

Dependencies:
  - httpx (already in tests/requirements.txt)
  - websockets (optional, only if you pass a ws_url)
"""

from __future__ import annotations

import asyncio
import json
import time
from typing import Any, Dict, Iterable, Optional, Tuple, Union

from tests.harness.clients import HttpRpcClient


HexInt = Union[str, int]


# ------------------------------------------------------------------------------
# Small helpers
# ------------------------------------------------------------------------------

def _hex_to_int(v: HexInt) -> int:
    if isinstance(v, int):
        return v
    if isinstance(v, str) and v.startswith("0x"):
        return int(v, 16)
    return int(v)


def _extract_block_number(obj: Any) -> Optional[int]:
    """
    Try best-effort to extract a block height from various shapes.
    """
    if obj is None:
        return None
    if isinstance(obj, (int,)):
        return obj
    if isinstance(obj, str):
        try:
            return _hex_to_int(obj)
        except Exception:
            return None
    if isinstance(obj, dict):
        for key in ("height", "number", "blockNumber"):
            if key in obj and obj[key] is not None:
                try:
                    return _hex_to_int(obj[key])
                except Exception:
                    continue
    return None


def _rpc_try_methods(
    rpc: HttpRpcClient,
    methods: Iterable[str],
    params: Iterable[Any] | None = None,
) -> Any:
    """
    Call the first RPC method that works. Uses HttpRpcClient.call_first if present,
    otherwise tries sequentially with .call(method, params).
    """
    params_list = list(params) if params is not None else []
    # Prefer client's own helper if available:
    cf = getattr(rpc, "call_first", None)
    if callable(cf):
        return cf(list(methods), params_list)

    # Fallback to manual attempts
    last_err = None
    for m in methods:
        try:
            call = getattr(rpc, "call", None)
            if callable(call):
                return call(m, params_list)
            # Very last resort: callable client
            if callable(rpc):
                return rpc(m, params_list)
        except Exception as e:
            last_err = e
            continue
    if last_err:
        raise last_err
    raise RuntimeError("RPC client does not expose a usable call interface")


def _get_head_height(rpc: HttpRpcClient) -> int:
    """
    Returns current head height as int.
    Tries multiple Omni/EVM-compatible methods.
    """
    # 1) Direct height
    res = _rpc_try_methods(
        rpc,
        methods=("omni_getHeadHeight", "omni_headHeight", "chain_headHeight", "eth_blockNumber"),
        params=[],
    )
    n = _extract_block_number(res)
    if n is not None:
        return n

    # 2) Full head object
    res = _rpc_try_methods(
        rpc,
        methods=("omni_getHead", "omni_head", "chain_getHead", "eth_getBlockByNumber"),
        params=(["latest", False] if "eth_getBlockByNumber" in ("eth_getBlockByNumber",) else []),
    )
    n = _extract_block_number(res)
    if n is not None:
        return n

    raise RuntimeError("Could not determine head height from RPC")


def _get_receipt(rpc: HttpRpcClient, tx_hash: str) -> Optional[Dict[str, Any]]:
    """
    Try several receipt endpoints.
    """
    # Common names:
    methods = (
        "omni_getReceipt",
        "omni_getTxReceipt",
        "eth_getTransactionReceipt",
    )
    try:
        res = _rpc_try_methods(rpc, methods, [tx_hash])
    except Exception:
        return None
    if not res:
        return None
    if isinstance(res, dict):
        return res
    return None


def _receipt_is_finalized(rcpt: Dict[str, Any]) -> bool:
    """
    Minimal 'final enough' signal: present and has a block number or status.
    """
    if rcpt is None:
        return False
    if rcpt.get("blockNumber") is not None:
        return True
    # Omni-style may include "status" or "success"
    if "status" in rcpt or "success" in rcpt:
        return True
    return False


# ------------------------------------------------------------------------------
# Public API
# ------------------------------------------------------------------------------

def wait_for_height(
    rpc: HttpRpcClient,
    target_height: int,
    *,
    timeout: float = 30.0,
    poll_interval: float = 0.5,
) -> int:
    """
    Wait until the chain reaches at least `target_height`.
    Returns the observed height (>= target), or raises TimeoutError.
    """
    deadline = time.time() + timeout
    last = None
    while time.time() < deadline:
        try:
            last = _get_head_height(rpc)
        except Exception:
            # If the node is booting, give it a moment
            time.sleep(min(poll_interval, 0.5))
            continue
        if last is not None and last >= target_height:
            return last
        time.sleep(poll_interval)
    raise TimeoutError(f"Timed out waiting for height {target_height} (last={last})")


async def _wait_for_new_head_ws(
    ws_url: str,
    *,
    start_height: int,
    min_delta: int,
    timeout: float,
) -> int:
    """
    Internal: await newHeads via JSON-RPC WS until height >= start_height + min_delta.
    """
    import websockets  # optional test dep

    deadline = time.time() + timeout
    async with websockets.connect(ws_url, ping_interval=20, close_timeout=2) as ws:
        # Try eth_subscribe first, then omni_subscribe.
        sub_msg_eth = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "eth_subscribe",
            "params": ["newHeads"],
        }
        sub_msg_omni = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "omni_subscribe",
            "params": ["newHeads"],
        }

        async def try_sub(msg: Dict[str, Any]) -> bool:
            await ws.send(json.dumps(msg))
            try:
                raw = await asyncio.wait_for(ws.recv(), timeout=5.0)
            except asyncio.TimeoutError:
                return False
            try:
                resp = json.loads(raw)
            except Exception:
                return False
            return "result" in resp

        if not await try_sub(sub_msg_eth):
            # try omni
            ok = await try_sub(sub_msg_omni)
            if not ok:
                # If subscribe fails, bail so caller can fall back to polling.
                raise RuntimeError("WS subscribe failed")

        target = start_height + max(1, min_delta)
        while time.time() < deadline:
            try:
                raw = await asyncio.wait_for(ws.recv(), timeout=deadline - time.time())
            except asyncio.TimeoutError:
                break
            try:
                msg = json.loads(raw)
            except Exception:
                continue
            # Notification shape: { "method":"eth_subscription","params":{"subscription":"...","result":{...}}}
            params = msg.get("params") if isinstance(msg, dict) else None
            result = params.get("result") if isinstance(params, dict) else None
            h = _extract_block_number(result)
            if h is not None and h >= target:
                return h
        raise TimeoutError("Timed out waiting for new head via WS")


def wait_for_new_head(
    rpc: HttpRpcClient,
    *,
    ws_url: Optional[str] = None,
    start_height: Optional[int] = None,
    min_delta: int = 1,
    timeout: float = 30.0,
    poll_interval: float = 0.5,
) -> int:
    """
    Wait for at least `min_delta` new blocks after `start_height`.
    If `start_height` is None, reads the current head first.

    If `ws_url` is provided, tries a WS subscription to 'newHeads' for
    responsiveness; falls back to HTTP polling on failure.

    Returns the observed head height.
    """
    base = _get_head_height(rpc) if start_height is None else start_height
    target = base + max(1, min_delta)

    # Try WS if provided
    if ws_url:
        try:
            return asyncio.run(_wait_for_new_head_ws(
                ws_url,
                start_height=base,
                min_delta=min_delta,
                timeout=timeout,
            ))
        except Exception:
            # Fall back to polling
            pass

    return wait_for_height(rpc, target, timeout=timeout, poll_interval=poll_interval)


def wait_for_receipt(
    rpc: HttpRpcClient,
    tx_hash: str,
    *,
    timeout: float = 60.0,
    poll_interval: float = 0.5,
    require_success: bool = False,
) -> Dict[str, Any]:
    """
    Poll for a transaction receipt until available (and optionally successful).
    Returns the receipt dict; raises TimeoutError on timeout.

    If require_success=True, raises RuntimeError if the receipt indicates failure.
    """
    deadline = time.time() + timeout
    last_rcpt = None
    while time.time() < deadline:
        rcpt = _get_receipt(rpc, tx_hash)
        if rcpt:
            last_rcpt = rcpt
            if _receipt_is_finalized(rcpt):
                # Optional success check: many chains use "status": 0x1 / 0x0 or True/False
                if require_success:
                    status = rcpt.get("status", rcpt.get("success"))
                    # Interpret commonly-seen forms
                    ok = True
                    if status is None:
                        ok = True  # treat absence as OK unless explicitly required
                    elif isinstance(status, bool):
                        ok = status
                    elif isinstance(status, (int,)):
                        ok = status != 0
                    elif isinstance(status, str) and status.startswith("0x"):
                        ok = int(status, 16) != 0
                    if not ok:
                        raise RuntimeError(f"Receipt indicates failure: {rcpt}")
                return rcpt
        time.sleep(poll_interval)
    raise TimeoutError(f"Timed out waiting for receipt {tx_hash} (last={last_rcpt})")


def wait_for_inclusion_and_receipt(
    rpc: HttpRpcClient,
    tx_hash: str,
    *,
    min_confirmations: int = 0,
    timeout: float = 90.0,
    poll_interval: float = 0.5,
    require_success: bool = True,
) -> Tuple[Dict[str, Any], int]:
    """
    Wait for a transaction receipt, then (optionally) for additional confirmations.
    Returns (receipt, current_head_height).
    """
    rcpt = wait_for_receipt(
        rpc,
        tx_hash,
        timeout=timeout,
        poll_interval=poll_interval,
        require_success=require_success,
    )
    bn = _extract_block_number(rcpt)
    if bn is None:
        # Some nodes return receipts without blockNumber; consider done.
        return rcpt, _get_head_height(rpc)

    if min_confirmations > 0:
        target = bn + min_confirmations
        h = wait_for_height(rpc, target, timeout=max(0.0, timeout / 2), poll_interval=poll_interval)
        return rcpt, h

    return rcpt, _get_head_height(rpc)
