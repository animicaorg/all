"""
Typed error classes for the Python SDK.

These are raised by rpc/http, tx/send, abi/encoding, and verify helpers so
callers can catch specific failure modes while still being able to catch the
base `OmniSdkError`.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import IntEnum
from typing import Any, Dict, Optional

__all__ = [
    "OmniSdkError",
    "RpcError",
    "TxError",
    "AbiError",
    "VerifyError",
    "JsonRpcCode",
    "from_jsonrpc_error",
    "raise_for_jsonrpc_result",
]


class OmniSdkError(Exception):
    """Base class for all SDK errors."""


class JsonRpcCode(IntEnum):
    # JSON-RPC 2.0 spec
    PARSE_ERROR = -32700
    INVALID_REQUEST = -32600
    METHOD_NOT_FOUND = -32601
    INVALID_PARAMS = -32602
    INTERNAL_ERROR = -32603

    # Server errors (implementation-defined range: -32099 to -32000)
    SERVER_ERROR = -32000

    # Common custom extensions (kept as hints)
    RATE_LIMITED = -32001
    UNAUTHORIZED = -32002
    CHAIN_MISMATCH = -32010
    TX_REJECTED = -32011


@dataclass(slots=True)
class RpcError(OmniSdkError):
    """Raised when a JSON-RPC call returns an error object."""

    method: Optional[str]
    code: int
    message: str
    data: Optional[Any] = None
    request_id: Optional[Any] = None
    http_status: Optional[int] = None

    def __str__(self) -> str:  # pragma: no cover - trivial
        parts = [f"RPC[{self.method or '-'}] code={self.code} msg={self.message!r}"]
        if self.request_id is not None:
            parts.append(f"id={self.request_id}")
        if self.http_status is not None:
            parts.append(f"http={self.http_status}")
        if self.data is not None:
            parts.append(f"data={self.data!r}")
        return " ".join(parts)

    @property
    def code_enum(self) -> Optional[JsonRpcCode]:
        try:
            return JsonRpcCode(self.code)
        except ValueError:
            return None


@dataclass(slots=True)
class TxError(OmniSdkError):
    """
    Raised when a submitted transaction fails (node rejection or on-chain revert).

    Fields:
      - tx_hash: hex hash if known (may be None if rejected pre-broadcast)
      - code: implementation-specific error/revert code (if available)
      - message: human-readable description
      - receipt: optional partial receipt/body with more context
    """

    message: str
    tx_hash: Optional[str] = None
    code: Optional[int] = None
    receipt: Optional[Dict[str, Any]] = None

    def __str__(self) -> str:  # pragma: no cover - trivial
        suffix = f" tx={self.tx_hash}" if self.tx_hash else ""
        code = f" code={self.code}" if self.code is not None else ""
        return f"TxError{suffix}{code}: {self.message}"


@dataclass(slots=True)
class AbiError(OmniSdkError):
    """
    Raised when ABI encoding/decoding or validation fails.

    Typical causes: wrong arg types/lengths, out-of-range integers, bad bytes hex.
    """

    message: str
    function: Optional[str] = None
    parameter: Optional[str] = None
    details: Optional[str] = None

    def __str__(self) -> str:  # pragma: no cover - trivial
        where = []
        if self.function:
            where.append(f"fn={self.function}")
        if self.parameter:
            where.append(f"param={self.parameter}")
        where_s = (" [" + ", ".join(where) + "]") if where else ""
        return f"AbiError{where_s}: {self.message}"


@dataclass(slots=True)
class VerifyError(OmniSdkError):
    """
    Raised when source verification fails to match an on-chain code hash, or when
    a verification job reports failure.
    """

    message: str
    address: Optional[str] = None
    expected_code_hash: Optional[str] = None
    got_code_hash: Optional[str] = None
    tx_hash: Optional[str] = None
    reason: Optional[str] = None

    def __str__(self) -> str:  # pragma: no cover - trivial
        bits = [self.message]
        if self.address:
            bits.append(f"addr={self.address}")
        if self.expected_code_hash or self.got_code_hash:
            bits.append(f"expected={self.expected_code_hash} got={self.got_code_hash}")
        if self.tx_hash:
            bits.append(f"tx={self.tx_hash}")
        if self.reason:
            bits.append(f"reason={self.reason}")
        return "VerifyError: " + " ".join(bits)


def from_jsonrpc_error(
    err_obj: Dict[str, Any],
    *,
    method: Optional[str] = None,
    request_id: Optional[Any] = None,
    http_status: Optional[int] = None,
) -> RpcError:
    """
    Convert a JSON-RPC error object into RpcError.

    `err_obj` should resemble: {"code": int, "message": str, "data": any?}
    """
    code = int(err_obj.get("code", JsonRpcCode.SERVER_ERROR))
    message = str(err_obj.get("message", "Unknown JSON-RPC error"))
    data = err_obj.get("data")
    return RpcError(
        method=method,
        code=code,
        message=message,
        data=data,
        request_id=request_id,
        http_status=http_status,
    )


def raise_for_jsonrpc_result(
    result: Dict[str, Any],
    *,
    method: Optional[str] = None,
    http_status: Optional[int] = None,
) -> None:
    """
    If `result` contains an "error" field, raise RpcError.

    This is a convenience to be called by the HTTP client after parsing a JSON-RPC response.
    """
    if "error" in result and result["error"] is not None:
        rid = result.get("id")
        raise from_jsonrpc_error(
            result["error"], method=method, request_id=rid, http_status=http_status
        )
