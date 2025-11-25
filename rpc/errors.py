"""
JSON-RPC errors for Animica.

This module provides:
- Canonical JSON-RPC 2.0 error codes (parse/invalid request/method not found/invalid params/internal).
- Chain-specific server error codes in the reserved -32000..-32099 range.
- Exception classes that carry (code, message, data).
- Helpers to convert arbitrary exceptions → JSON-RPC error envelopes.
- An optional HTTP status hint mapper for gateways.

Usage (from rpc/jsonrpc.py):
    from .errors import RpcError, to_error, error_response

    try:
        result = handle(method, params)
        return {"jsonrpc": "2.0", "id": req_id, "result": result}
    except Exception as e:
        err = to_error(e)
        return error_response(req_id, err)

Notes:
- `data` SHOULD be small, stable, and safe to expose. Avoid raw tracebacks in prod.
- All custom codes live in -32000..-32099 and are stable across releases.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import IntEnum
from typing import Any, Dict, Mapping, Optional, Union


# ───────────────────────────────────────────────────────────────────────────────
# JSON-RPC 2.0 codes (spec)
# ───────────────────────────────────────────────────────────────────────────────

class JsonRpcCode(IntEnum):
    PARSE_ERROR = -32700
    INVALID_REQUEST = -32600
    METHOD_NOT_FOUND = -32601
    INVALID_PARAMS = -32602
    INTERNAL_ERROR = -32603
    # -32000 to -32099: Server error (reserved for app-defined codes)


# ───────────────────────────────────────────────────────────────────────────────
# Animica server codes (-32000..-32099)
# Keep these stable; append new codes at the end to avoid collisions.
# ───────────────────────────────────────────────────────────────────────────────

class AnimicaCode(IntEnum):
    SERVER_ERROR = -32000
    RATE_LIMITED = -32001
    TEMPORARILY_UNAVAILABLE = -32002
    ACCESS_DENIED = -32003
    NOT_FOUND = -32004
    ALREADY_EXISTS = -32005

    # TX & state
    INVALID_TX = -32010
    CHAIN_ID_MISMATCH = -32011
    BAD_SIGNATURE = -32012
    INSUFFICIENT_FUNDS = -32013
    NONCE_TOO_LOW = -32014
    NONCE_TOO_HIGH = -32015
    GAS_TOO_LOW = -32016
    FEE_TOO_LOW = -32017
    TX_TOO_LARGE = -32018
    MEMPOOL_FULL = -32019
    DUPLICATE_TX = -3201_0  # -32010 is used; we’ll map duplicate specially below (see _CANON_MAP)

    # Consensus/Proofs
    BAD_PROOF = -32030
    POIES_REJECTED = -32031
    PQ_POLICY_VIOLATION = -32032

    # Data availability
    DA_ERROR = -32040
    DA_NOT_AVAILABLE = -32041

    # Randomness
    RAND_WINDOW_ERROR = -32050
    VDF_INVALID = -32051

# Back-compat: fix typo for DUPLICATE_TX (ensure distinct code)
AnimicaCode.DUPLICATE_TX = IntEnum("AnimicaCode", {"DUPLICATE_TX": -32020})(-32020)  # type: ignore


# ───────────────────────────────────────────────────────────────────────────────
# Error dataclass & base exception
# ───────────────────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class RpcError(Exception):
    code: int
    message: str
    data: Optional[Mapping[str, Any]] = None

    def to_dict(self) -> Dict[str, Any]:
        err: Dict[str, Any] = {"code": int(self.code), "message": str(self.message)}
        if self.data:
            err["data"] = _safe_jsonable(self.data)
        return err

    def __str__(self) -> str:  # pragma: no cover
        return f"[{self.code}] {self.message} ({self.data})"


# ───────────────────────────────────────────────────────────────────────────────
# Concrete exception types (nice to catch in handlers)
# ───────────────────────────────────────────────────────────────────────────────

class ParseError(RpcError):
    def __init__(self, detail: str = "Parse error", **data: Any) -> None:
        super().__init__(JsonRpcCode.PARSE_ERROR, detail, data or None)

class InvalidRequest(RpcError):
    def __init__(self, detail: str = "Invalid request", **data: Any) -> None:
        super().__init__(JsonRpcCode.INVALID_REQUEST, detail, data or None)

class MethodNotFound(RpcError):
    def __init__(self, method: str) -> None:
        super().__init__(JsonRpcCode.METHOD_NOT_FOUND, "Method not found", {"method": method})

class InvalidParams(RpcError):
    def __init__(self, detail: str = "Invalid params", **data: Any) -> None:
        super().__init__(JsonRpcCode.INVALID_PARAMS, detail, data or None)

class InternalError(RpcError):
    def __init__(self, detail: str = "Internal error", **data: Any) -> None:
        super().__init__(JsonRpcCode.INTERNAL_ERROR, detail, data or None)

# Server errors
class ServerError(RpcError):
    def __init__(self, detail: str = "Server error", **data: Any) -> None:
        super().__init__(AnimicaCode.SERVER_ERROR, detail, data or None)

class RateLimited(RpcError):
    def __init__(self, retry_after_ms: Optional[int] = None, **data: Any) -> None:
        payload = dict(data or {})
        if retry_after_ms is not None:
            payload["retryAfterMs"] = int(retry_after_ms)
        super().__init__(AnimicaCode.RATE_LIMITED, "Too many requests", payload or None)

class TemporarilyUnavailable(RpcError):
    def __init__(self, detail: str = "Temporarily unavailable", **data: Any) -> None:
        super().__init__(AnimicaCode.TEMPORARILY_UNAVAILABLE, detail, data or None)

class AccessDenied(RpcError):
    def __init__(self, detail: str = "Access denied", **data: Any) -> None:
        super().__init__(AnimicaCode.ACCESS_DENIED, detail, data or None)

class NotFound(RpcError):
    def __init__(self, what: str = "resource", **data: Any) -> None:
        super().__init__(AnimicaCode.NOT_FOUND, f"{what} not found", data or None)

class AlreadyExists(RpcError):
    def __init__(self, what: str = "resource", **data: Any) -> None:
        super().__init__(AnimicaCode.ALREADY_EXISTS, f"{what} already exists", data or None)

# Tx & state
class InvalidTx(RpcError):
    def __init__(self, reason: str = "Invalid transaction", **data: Any) -> None:
        super().__init__(AnimicaCode.INVALID_TX, reason, data or None)

class ChainIdMismatch(RpcError):
    def __init__(self, got: int, expected: int) -> None:
        super().__init__(AnimicaCode.CHAIN_ID_MISMATCH, "Chain ID mismatch", {"got": got, "expected": expected})

class BadSignature(RpcError):
    def __init__(self, detail: str = "Bad or unsupported signature", **data: Any) -> None:
        super().__init__(AnimicaCode.BAD_SIGNATURE, detail, data or None)

class InsufficientFunds(RpcError):
    def __init__(self, required: int, available: int) -> None:
        super().__init__(AnimicaCode.INSUFFICIENT_FUNDS, "Insufficient funds", {"required": str(required), "available": str(available)})

class NonceTooLow(RpcError):
    def __init__(self, got: int, expected: int) -> None:
        super().__init__(AnimicaCode.NONCE_TOO_LOW, "Nonce too low", {"got": got, "expected": expected})

class NonceTooHigh(RpcError):
    def __init__(self, got: int, highest: int) -> None:
        super().__init__(AnimicaCode.NONCE_TOO_HIGH, "Nonce too high", {"got": got, "highest": highest})

class GasTooLow(RpcError):
    def __init__(self, got: int, min_required: int) -> None:
        super().__init__(AnimicaCode.GAS_TOO_LOW, "Gas too low", {"got": got, "minRequired": min_required})

class FeeTooLow(RpcError):
    def __init__(self, got: int, floor: int) -> None:
        super().__init__(AnimicaCode.FEE_TOO_LOW, "Fee too low", {"got": str(got), "floor": str(floor)})

class TxTooLarge(RpcError):
    def __init__(self, got_bytes: int, max_bytes: int) -> None:
        super().__init__(AnimicaCode.TX_TOO_LARGE, "Transaction too large", {"gotBytes": got_bytes, "maxBytes": max_bytes})

class MempoolFull(RpcError):
    def __init__(self, limit: int) -> None:
        super().__init__(AnimicaCode.MEMPOOL_FULL, "Mempool full", {"limit": limit})

class DuplicateTx(RpcError):
    def __init__(self, tx_hash: str) -> None:
        super().__init__(AnimicaCode.DUPLICATE_TX, "Duplicate transaction", {"hash": tx_hash})

# Consensus/Proofs
class BadProof(RpcError):
    def __init__(self, kind: str, detail: str = "Invalid proof") -> None:
        super().__init__(AnimicaCode.BAD_PROOF, detail, {"kind": kind})

class PoIESRejected(RpcError):
    def __init__(self, score: float, theta: float) -> None:
        super().__init__(AnimicaCode.POIES_REJECTED, "Block below acceptance threshold", {"score": score, "theta": theta})

class PqPolicyViolation(RpcError):
    def __init__(self, detail: str = "PQ policy violation", **data: Any) -> None:
        super().__init__(AnimicaCode.PQ_POLICY_VIOLATION, detail, data or None)

# DA
class DaError(RpcError):
    def __init__(self, detail: str = "Data-availability error", **data: Any) -> None:
        super().__init__(AnimicaCode.DA_ERROR, detail, data or None)

class DaNotAvailable(RpcError):
    def __init__(self, commitment: str) -> None:
        super().__init__(AnimicaCode.DA_NOT_AVAILABLE, "Blob not available", {"commitment": commitment})

# Randomness
class RandWindowError(RpcError):
    def __init__(self, detail: str = "Commit/reveal outside window", **data: Any) -> None:
        super().__init__(AnimicaCode.RAND_WINDOW_ERROR, detail, data or None)

class VdfInvalid(RpcError):
    def __init__(self, detail: str = "Invalid VDF proof", **data: Any) -> None:
        super().__init__(AnimicaCode.VDF_INVALID, detail, data or None)


# ───────────────────────────────────────────────────────────────────────────────
# Helpers
# ───────────────────────────────────────────────────────────────────────────────

def error_response(req_id: Optional[Union[str, int]], err: RpcError) -> Dict[str, Any]:
    """
    Build a JSON-RPC error response dict.
    """
    return {"jsonrpc": "2.0", "id": req_id, "error": err.to_dict()}


def _safe_jsonable(obj: Any) -> Any:
    """
    Best-effort sanitizer: convert exotic objects to strings, ints, or dicts.
    Keeps nested dicts/lists as-is if they already contain JSON-serializable items.
    """
    try:
        # Quick path: primitives
        if obj is None or isinstance(obj, (bool, int, float, str)):
            return obj
        if isinstance(obj, (bytes, bytearray)):
            return "0x" + bytes(obj).hex()
        if isinstance(obj, dict):
            return {str(k): _safe_jsonable(v) for k, v in obj.items()}
        if isinstance(obj, (list, tuple)):
            return [_safe_jsonable(x) for x in obj]
        # dataclass?
        if hasattr(obj, "__dict__"):
            return {k: _safe_jsonable(v) for k, v in vars(obj).items()}
        # mapping?
        if isinstance(obj, Mapping):
            return {str(k): _safe_jsonable(v) for k, v in obj.items()}
        return str(obj)
    except Exception:
        return str(obj)


def http_status_hint(code: int) -> int:
    """
    Optional mapping to HTTP status for JSON-RPC-over-HTTP gateways.
    (This is advisory; JSON-RPC spec is transport-agnostic.)
    """
    if code in (JsonRpcCode.PARSE_ERROR, JsonRpcCode.INVALID_REQUEST, JsonRpcCode.INVALID_PARAMS):
        return 400
    if code == JsonRpcCode.METHOD_NOT_FOUND:
        return 404
    if code in (AnimicaCode.ACCESS_DENIED,):
        return 403
    if code in (AnimicaCode.RATE_LIMITED,):
        return 429
    if code in (AnimicaCode.NOT_FOUND,):
        return 404
    if code in (AnimicaCode.ALREADY_EXISTS, AnimicaCode.DUPLICATE_TX):
        return 409
    if code in (AnimicaCode.TEMPORARILY_UNAVAILABLE,):
        return 503
    # Default server-side hiccup
    if code in (JsonRpcCode.INTERNAL_ERROR, AnimicaCode.SERVER_ERROR):
        return 500
    # Fallback
    return 400


# Map common Python/Animica exceptions to RpcError for convenience.
_CANON_MAP = {
    KeyError: lambda e: NotFound(what=str(e.args[0]) if e.args else "resource"),
    PermissionError: lambda e: AccessDenied(str(e)),
    TimeoutError: lambda e: TemporarilyUnavailable("Timeout"),
    ValueError: lambda e: InvalidParams(str(e)),
}


def to_error(exc: Exception) -> RpcError:
    """
    Convert any Exception into a RpcError.
    - If it's already RpcError, pass through.
    - If it's a known Animica exception (e.g., InvalidTx), pass through.
    - If it's a common Python error (KeyError/ValueError/etc.), map to a friendly RpcError.
    - Otherwise return InternalError with a terse message.
    """
    if isinstance(exc, RpcError):
        return exc
    for typ, fn in _CANON_MAP.items():
        if isinstance(exc, typ):
            try:
                return fn(exc)  # type: ignore[misc]
            except Exception:
                break
    # Last resort: generic internal
    return InternalError(detail="Internal error", reason=exc.__class__.__name__)


__all__ = [
    # dataclass
    "RpcError",
    # enums
    "JsonRpcCode",
    "AnimicaCode",
    # canonical JSON-RPC
    "ParseError",
    "InvalidRequest",
    "MethodNotFound",
    "InvalidParams",
    "InternalError",
    # server/general
    "ServerError",
    "RateLimited",
    "TemporarilyUnavailable",
    "AccessDenied",
    "NotFound",
    "AlreadyExists",
    # tx/state
    "InvalidTx",
    "ChainIdMismatch",
    "BadSignature",
    "InsufficientFunds",
    "NonceTooLow",
    "NonceTooHigh",
    "GasTooLow",
    "FeeTooLow",
    "TxTooLarge",
    "MempoolFull",
    "DuplicateTx",
    # proofs/consensus
    "BadProof",
    "PoIESRejected",
    "PqPolicyViolation",
    # DA
    "DaError",
    "DaNotAvailable",
    # randomness
    "RandWindowError",
    "VdfInvalid",
    # helpers
    "error_response",
    "to_error",
    "http_status_hint",
]
