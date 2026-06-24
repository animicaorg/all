"""Bitcoin Core RPC error codes for the compatibility facade.

Handlers raise these via ``rpc_error(code, message)`` so the existing JSON-RPC
dispatcher serializes ``error.code``/``error.message`` exactly as Bitcoin clients
expect. Native Animica errors are remapped (not leaked) via ``to_btc_error``.
"""

from __future__ import annotations

from typing import Any

# Bitcoin Core RPC error codes (bitcoin/src/rpc/protocol.h)
RPC_MISC_ERROR = -1
RPC_TYPE_ERROR = -3
RPC_WALLET_ERROR = -4
RPC_INVALID_ADDRESS_OR_KEY = -5
RPC_WALLET_INSUFFICIENT_FUNDS = -6
RPC_INVALID_PARAMETER = -8
RPC_CLIENT_NODE_NOT_CONNECTED = -9
RPC_DATABASE_ERROR = -20
RPC_DESERIALIZATION_ERROR = -22
RPC_VERIFY_ERROR = -25
RPC_VERIFY_REJECTED = -26
RPC_VERIFY_ALREADY_IN_CHAIN = -27
RPC_IN_WARMUP = -28
RPC_METHOD_NOT_FOUND = -32601
RPC_INVALID_REQUEST = -32600
RPC_PARSE_ERROR = -32700


def _RpcError():
    # Lazy import so this module loads even if rpc.errors layout shifts.
    try:
        from rpc.errors import RpcError  # type: ignore
        return RpcError
    except Exception:  # pragma: no cover
        from rpc.jsonrpc import RpcError  # type: ignore
        return RpcError


def rpc_error(code: int, message: str, data: Any = None):
    """Build the project's RpcError with a Bitcoin error code."""
    RpcError = _RpcError()
    try:
        return RpcError(code=code, message=message, data=data)
    except TypeError:
        # Some RpcError variants take positional (message, code)
        try:
            return RpcError(message, code)  # type: ignore
        except Exception:
            e = RpcError(message)  # type: ignore
            try:
                e.code = code  # type: ignore
            except Exception:
                pass
            return e


# Animica failure substrings/codes -> (bitcoin_code, reject_reason)
_REASON_MAP = [
    ("insufficient", RPC_VERIFY_REJECTED, "insufficient funds"),
    ("nonce too low", RPC_VERIFY_ALREADY_IN_CHAIN, "txn-already-known"),
    ("already known", RPC_VERIFY_ALREADY_IN_CHAIN, "txn-already-known"),
    ("already in mempool", RPC_VERIFY_ALREADY_IN_CHAIN, "txn-already-in-mempool"),
    ("duplicate", RPC_VERIFY_ALREADY_IN_CHAIN, "txn-already-known"),
    ("nonce too high", RPC_VERIFY_REJECTED, "non-final"),
    ("nonce gap", RPC_VERIFY_REJECTED, "non-final"),
    ("fee too low", RPC_VERIFY_REJECTED, "min relay fee not met"),
    ("min relay", RPC_VERIFY_REJECTED, "min relay fee not met"),
    ("too large", RPC_VERIFY_REJECTED, "tx-size"),
    ("mempool full", RPC_VERIFY_REJECTED, "mempool full"),
    ("chain id", RPC_VERIFY_REJECTED, "bad-txns-chainid"),
    ("chainid", RPC_VERIFY_REJECTED, "bad-txns-chainid"),
    ("signature", RPC_VERIFY_REJECTED, "mandatory-script-verify-flag-failed"),
    ("bad sig", RPC_VERIFY_REJECTED, "mandatory-script-verify-flag-failed"),
    ("decode", RPC_DESERIALIZATION_ERROR, "TX decode failed"),
    ("deserial", RPC_DESERIALIZATION_ERROR, "TX decode failed"),
    ("not found", RPC_INVALID_ADDRESS_OR_KEY, "No such mempool or blockchain transaction"),
]


def reason_for(message: str) -> tuple[int, str]:
    """Map an Animica error message to (bitcoin_code, reject_reason_string)."""
    m = (message or "").lower()
    for needle, code, reason in _REASON_MAP:
        if needle in m:
            return code, reason
    return RPC_VERIFY_REJECTED, (message or "rejected")


def to_btc_error(exc: Exception, *, default_code: int = RPC_MISC_ERROR):
    """Convert any native exception into a Bitcoin-coded RpcError."""
    msg = str(exc) or exc.__class__.__name__
    code, reason = reason_for(msg)
    # If nothing matched our reject map, prefer the default code.
    if reason == (msg or "rejected"):
        code = default_code
    return rpc_error(code, msg)
