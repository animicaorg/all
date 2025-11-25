"""
omni_sdk.tx.send
================

Submit raw CBOR transactions to a node via JSON-RPC and await receipts.

Primary entry points
--------------------
- submit_raw(rpc, raw_tx: bytes) -> str
    Sends the raw CBOR-encoded transaction via `tx.sendRawTransaction`.
    Returns the transaction hash as a hex string (0x-prefixed).

- wait_for_receipt(rpc, tx_hash: str, *, timeout_s=60, poll_interval_s=0.5) -> dict
    Polls `tx.getTransactionReceipt` until a receipt is available or timeout.

- submit_and_wait(rpc, raw_tx: bytes, *, timeout_s=60, poll_interval_s=0.5) -> dict
    Convenience wrapper: submit via RPC then wait for the receipt with polling.

Optional WebSocket assist
-------------------------
If you pass a `ws` client from `omni_sdk.rpc.ws` to `wait_for_receipt_ws` or
`submit_and_wait_ws`, the wait loop will subscribe to `newHeads` and re-check on
each head, reducing needless polls. If WS is unavailable, fall back to polling.

We do not assume any proprietary push-of-receipts topic; head-triggered polling
is robust and compatible with the node described in the spec.
"""

from __future__ import annotations

import time
from typing import Any, Dict, Optional, Protocol

# Errors surfaced by SDK
try:
    from omni_sdk.errors import RpcError, TxError  # type: ignore
except Exception:
    class RpcError(RuntimeError):
        pass
    class TxError(RuntimeError):
        pass

# Small utils
try:
    from omni_sdk.utils.bytes import to_hex as _to_hex  # type: ignore
except Exception:
    def _to_hex(b: bytes) -> str:
        return "0x" + bytes(b).hex()

try:
    from omni_sdk.utils.hash import sha3_256 as _sha3_256  # type: ignore
except Exception:
    import hashlib
    def _sha3_256(x: bytes) -> bytes:
        return hashlib.sha3_256(x).digest()


# -----------------------------------------------------------------------------
# Minimal client protocols to avoid tight coupling with concrete implementations
# -----------------------------------------------------------------------------

class _RpcClient(Protocol):
    """
    Minimal interface expected from omni_sdk.rpc.http client.
    """
    def call(self, method: str, params: Optional[dict | list] = None) -> Any: ...


class _WsSubscription(Protocol):
    def __iter__(self): ...
    def __next__(self): ...


class _WsClient(Protocol):
    """
    Minimal interface expected from omni_sdk.rpc.ws client.
    We try .subscribe(topic) first; as a fallback we try .subscribe_sync(topic).
    """
    def subscribe(self, topic: str) -> _WsSubscription: ...
    # Optional alternative name used by some clients
    def subscribe_sync(self, topic: str) -> _WsSubscription: ...  # type: ignore[empty-body]


# -----------------------------------------------------------------------------
# Core RPC calls
# -----------------------------------------------------------------------------

def submit_raw(rpc: _RpcClient, raw_tx: bytes) -> str:
    """
    Submit a raw CBOR transaction to the node.

    Returns the transaction hash as a 0x-prefixed hex string.
    """
    if not isinstance(raw_tx, (bytes, bytearray)):
        raise TypeError("raw_tx must be bytes")

    try:
        # Per spec/openrpc: method name is tx.sendRawTransaction, param is hex or bytes.
        # Our HTTP client is expected to base64/hex wrap as needed; we pass raw bytes.
        result = rpc.call("tx.sendRawTransaction", [bytes(raw_tx)])
    except Exception as e:  # Map transport/errors to a stable type
        raise RpcError(f"tx.sendRawTransaction failed: {e}") from e

    if not isinstance(result, (str, bytes)):
        raise TxError(f"unexpected RPC result for sendRawTransaction: {type(result)!r}")
    # Normalize to hex string
    if isinstance(result, bytes):
        return _to_hex(result)
    if result.startswith("0x"):
        return result
    # Accept plain hex without prefix
    return "0x" + result


def get_transaction_receipt(rpc: _RpcClient, tx_hash: str) -> Optional[Dict[str, Any]]:
    """
    Query the node for a transaction receipt.

    Returns:
        dict receipt if available, or None if the tx is pending/not found yet.
    """
    try:
        res = rpc.call("tx.getTransactionReceipt", [tx_hash])
    except Exception as e:
        raise RpcError(f"tx.getTransactionReceipt failed: {e}") from e

    if res in (None, False, ""):
        return None
    if not isinstance(res, dict):
        # Some nodes may wrap as {"receipt": {...}}; unwrap if present.
        if isinstance(res, dict) and "receipt" in res and isinstance(res["receipt"], dict):
            return res["receipt"]
        raise TxError(f"unexpected receipt payload: {type(res)!r}")
    return res


# -----------------------------------------------------------------------------
# Polling waiters
# -----------------------------------------------------------------------------

def wait_for_receipt(
    rpc: _RpcClient,
    tx_hash: str,
    *,
    timeout_s: float = 60.0,
    poll_interval_s: float = 0.5,
    max_interval_s: float = 2.5,
    backoff: float = 1.25,
) -> Dict[str, Any]:
    """
    Poll for a receipt until it arrives or timeout is reached.

    Raises:
        TimeoutError on timeout
        RpcError / TxError on RPC or data-shape errors
    """
    deadline = time.monotonic() + float(timeout_s)
    interval = float(poll_interval_s)

    while True:
        rec = get_transaction_receipt(rpc, tx_hash)
        if rec is not None:
            return rec

        if time.monotonic() >= deadline:
            raise TimeoutError(f"timeout waiting for receipt (tx={tx_hash}, timeout_s={timeout_s})")

        time.sleep(interval)
        interval = min(interval * float(backoff), float(max_interval_s))


def submit_and_wait(
    rpc: _RpcClient,
    raw_tx: bytes,
    *,
    timeout_s: float = 60.0,
    poll_interval_s: float = 0.5,
) -> Dict[str, Any]:
    """
    Submit a raw tx and block until its receipt is available (polling).
    """
    txh = submit_raw(rpc, raw_tx)
    return wait_for_receipt(
        rpc,
        txh,
        timeout_s=timeout_s,
        poll_interval_s=poll_interval_s,
    )


# -----------------------------------------------------------------------------
# WebSocket-assisted waiters (optional)
# -----------------------------------------------------------------------------

def _sub(ws: _WsClient, topic: str) -> _WsSubscription:
    if hasattr(ws, "subscribe"):
        return ws.subscribe(topic)  # type: ignore[attr-defined]
    if hasattr(ws, "subscribe_sync"):
        return ws.subscribe_sync(topic)  # type: ignore[attr-defined]
    raise AttributeError("ws client does not support subscribe/subscribe_sync")


def wait_for_receipt_ws(
    rpc: _RpcClient,
    ws: _WsClient,
    tx_hash: str,
    *,
    timeout_s: float = 60.0,
    idle_check_s: float = 5.0,
) -> Dict[str, Any]:
    """
    Subscribe to newHeads via WS and re-check the receipt when a head arrives.
    Falls back to periodic checks every `idle_check_s` in case of low head rate.

    This is synchronous and blocks the current thread.
    """
    deadline = time.monotonic() + float(timeout_s)

    # Initial quick check
    rec = get_transaction_receipt(rpc, tx_hash)
    if rec is not None:
        return rec

    sub = _sub(ws, "newHeads")
    last_check = 0.0

    for _evt in sub:
        # On each head, check once
        rec = get_transaction_receipt(rpc, tx_hash)
        if rec is not None:
            return rec

        now = time.monotonic()
        if now - last_check >= float(idle_check_s):
            rec = get_transaction_receipt(rpc, tx_hash)
            if rec is not None:
                return rec
            last_check = now

        if now >= deadline:
            raise TimeoutError(f"timeout waiting for receipt (tx={tx_hash}, timeout_s={timeout_s})")

    # If the iterator ends unexpectedly, do one last poll then raise
    rec = get_transaction_receipt(rpc, tx_hash)
    if rec is not None:
        return rec
    raise TxError("WS subscription ended before receipt became available")


def submit_and_wait_ws(
    rpc: _RpcClient,
    ws: _WsClient,
    raw_tx: bytes,
    *,
    timeout_s: float = 60.0,
    idle_check_s: float = 5.0,
) -> Dict[str, Any]:
    """
    Submit a raw tx and wait for receipt using WS newHeads to trigger checks.

    If the WS subscription fails to start, this will raise accordingly. If you want
    a resilient flow that falls back to polling, wrap in try/except and call
    `submit_and_wait` on failure.
    """
    txh = submit_raw(rpc, raw_tx)
    return wait_for_receipt_ws(rpc, ws, txh, timeout_s=timeout_s, idle_check_s=idle_check_s)


__all__ = [
    "submit_raw",
    "get_transaction_receipt",
    "wait_for_receipt",
    "submit_and_wait",
    "wait_for_receipt_ws",
    "submit_and_wait_ws",
]
