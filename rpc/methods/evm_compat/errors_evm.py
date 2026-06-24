"""Ethereum JSON-RPC error codes for the EVM-compat facade."""

from __future__ import annotations

from typing import Any

# Standard JSON-RPC
RPC_PARSE_ERROR = -32700
RPC_INVALID_REQUEST = -32600
RPC_METHOD_NOT_FOUND = -32601
RPC_INVALID_PARAMS = -32602
RPC_INTERNAL_ERROR = -32603
# Ethereum server-error range
RPC_SERVER_ERROR = -32000          # generic (execution reverted, insufficient funds)
RPC_RESOURCE_NOT_FOUND = -32001
RPC_RESOURCE_UNAVAILABLE = -32002
RPC_TRANSACTION_REJECTED = -32003
RPC_METHOD_NOT_SUPPORTED = -32004
RPC_LIMIT_EXCEEDED = -32005


def _RpcError():
    try:
        from rpc.errors import RpcError  # type: ignore
        return RpcError
    except Exception:  # pragma: no cover
        from rpc.jsonrpc import RpcError  # type: ignore
        return RpcError


def rpc_error(code: int, message: str, data: Any = None):
    RpcError = _RpcError()
    try:
        return RpcError(code=code, message=message, data=data)
    except TypeError:
        try:
            return RpcError(message, code)  # type: ignore
        except Exception:
            e = RpcError(message)  # type: ignore
            try:
                e.code = code  # type: ignore
            except Exception:
                pass
            return e


def to_eth_error(exc: Exception, *, default_code: int = RPC_SERVER_ERROR):
    msg = str(exc) or exc.__class__.__name__
    low = msg.lower()
    if "insufficient" in low:
        return rpc_error(RPC_SERVER_ERROR, "insufficient funds for gas * price + value")
    if "nonce" in low:
        return rpc_error(RPC_SERVER_ERROR, "nonce too low")
    if "not found" in low:
        return rpc_error(RPC_RESOURCE_NOT_FOUND, msg)
    return rpc_error(default_code, msg)
