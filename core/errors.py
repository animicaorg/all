"""
Animica — core.errors
---------------------

A small, consistent error system for the node and libraries.

Design goals
------------
- One root `AnimicaError` with machine-friendly `code` and optional `data`.
- Concrete subclasses for common domains (config, db, genesis, codec, state, tx, block).
- Non-invasive helpers to enrich errors with contextual fields.
- Safe JSON representation (`to_dict`) suitable for logs and RPC bridges.
- Clear separation of *retryable* vs *permanent* failures.

This module uses only stdlib to avoid boot-time dependency cycles.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum, IntEnum
from typing import Any, Dict, Mapping, Optional, Type, TypeVar

# ---------------------------------------------------------------------------
# Error codes & classes
# ---------------------------------------------------------------------------


class Severity(IntEnum):
    """Optional severity hint for operators/metrics."""

    INFO = 20
    WARNING = 30
    ERROR = 40
    CRITICAL = 50


class CoreErrorCode(str, Enum):
    # Generic
    INTERNAL = "CORE/INTERNAL"
    NOT_IMPLEMENTED = "CORE/NOT_IMPLEMENTED"
    DEP_MISSING = "CORE/DEPENDENCY_MISSING"
    FEATURE_DISABLED = "CORE/FEATURE_DISABLED"
    RESOURCE_EXHAUSTED = "CORE/RESOURCE_EXHAUSTED"
    TIMEOUT = "CORE/TIMEOUT"

    # Config / environment / startup
    CONFIG = "CORE/CONFIG"
    ENVIRONMENT = "CORE/ENVIRONMENT"

    # Encoding / decoding / schema
    SERIALIZATION = "CORE/SERIALIZATION"
    DESERIALIZATION = "CORE/DESERIALIZATION"
    CDDL_VALIDATION = "CORE/CDDL_VALIDATION"
    SCHEMA_VALIDATION = "CORE/SCHEMA_VALIDATION"
    HASH_MISMATCH = "CORE/HASH_MISMATCH"

    # DB / storage
    DB = "CORE/DB"
    DB_NOT_FOUND = "CORE/DB_NOT_FOUND"
    DB_CONFLICT = "CORE/DB_CONFLICT"

    # Chain / genesis / headers / blocks
    GENESIS = "CORE/GENESIS"
    CHAIN_ID_MISMATCH = "CORE/CHAIN_ID_MISMATCH"
    HEADER_INVALID = "CORE/HEADER_INVALID"
    BLOCK_INVALID = "CORE/BLOCK_INVALID"
    STATE_INVARIANT = "CORE/STATE_INVARIANT"

    # Transactions (stateless/stateful)
    TX_INVALID = "CORE/TX_INVALID"
    TX_NONCE_GAP = "CORE/TX_NONCE_GAP"
    TX_INSUFFICIENT_BAL = "CORE/TX_INSUFFICIENT_BALANCE"
    TX_OOG = "CORE/TX_OUT_OF_GAS"

    # Networking / IO
    IO = "CORE/IO"
    NETWORK = "CORE/NETWORK"


@dataclass(eq=False)
class AnimicaError(Exception):
    """
    Root error for Animica components.

    Attributes
    ----------
    code: str
        Machine-stable error code (see CoreErrorCode).
    message: str
        Human hint suitable for logs; avoid leaking secrets.
    data: dict
        Optional machine data (hashes, ids, sizes). Must be JSON-serializable.
    severity: Severity
        Optional severity hint (default ERROR).
    retryable: bool
        Whether the operation may succeed on retry without changing inputs.
    cause: Optional[BaseException]
        Wrapped original exception; not included in equality comparison.
    """

    code: str
    message: str
    data: Dict[str, Any] = field(default_factory=dict)
    severity: Severity = Severity.ERROR
    retryable: bool = False
    cause: Optional[BaseException] = field(default=None, compare=False, repr=False)

    def __post_init__(self) -> None:
        # Make Exception(args) meaningful for interop
        super().__init__(f"{self.code}: {self.message}")

    # ---------------- Public API ----------------

    def with_context(self, **ctx: Any) -> "AnimicaError":
        """Return a *new* error with extra context merged (does not mutate)."""
        d = dict(self.data)
        for k, v in ctx.items():
            d[k] = _coerce_json(v)
        return type(self)(
            code=self.code,
            message=self.message,
            data=d,
            severity=self.severity,
            retryable=self.retryable,
            cause=self.cause,
        )

    def with_cause(self, exc: BaseException) -> "AnimicaError":
        """Attach/replace the causal exception (returns a new instance)."""
        return type(self)(
            code=self.code,
            message=self.message,
            data=dict(self.data),
            severity=self.severity,
            retryable=self.retryable,
            cause=exc,
        )

    def to_dict(self, include_cause: bool = False) -> Dict[str, Any]:
        """JSON-safe shape suitable for logs/RPC bridges."""
        out = {
            "code": self.code,
            "message": self.message,
            "data": _coerce_json(self.data),
            "severity": int(self.severity),
            "retryable": self.retryable,
        }
        if include_cause and self.cause is not None:
            out["cause"] = {
                "type": type(self.cause).__name__,
                "message": str(self.cause),
            }
        return out

    def __str__(self) -> str:  # pragma: no cover - human formatting
        parts = [f"{self.code}: {self.message}"]
        if self.data:
            preview = ", ".join(f"{k}={_preview(v)}" for k, v in self.data.items())
            parts.append(f"[{preview}]")
        return " ".join(parts)


# Concrete subclasses (thin wrappers for ergonomics)
class InternalError(AnimicaError):
    def __init__(self, message="internal error", **data: Any) -> None:
        super().__init__(
            code=CoreErrorCode.INTERNAL, message=message, data=_jsonmap(data)
        )


class NotImplementedFeature(AnimicaError):
    def __init__(self, message="feature not implemented", **data: Any) -> None:
        super().__init__(
            code=CoreErrorCode.NOT_IMPLEMENTED, message=message, data=_jsonmap(data)
        )


class DependencyMissing(AnimicaError):
    def __init__(self, package: str, hint: str = "") -> None:
        msg = f"missing dependency: {package}"
        if hint:
            msg += f" ({hint})"
        super().__init__(
            code=CoreErrorCode.DEP_MISSING,
            message=msg,
            data={"package": package, "hint": hint},
            retryable=False,
        )


class FeatureDisabled(AnimicaError):
    def __init__(self, feature: str, reason: str = "") -> None:
        super().__init__(
            code=CoreErrorCode.FEATURE_DISABLED,
            message=f"feature disabled: {feature}",
            data={"feature": feature, "reason": reason},
        )


class ConfigError(AnimicaError):
    def __init__(self, message="invalid configuration", **data: Any) -> None:
        super().__init__(
            code=CoreErrorCode.CONFIG,
            message=message,
            data=_jsonmap(data),
            retryable=False,
        )


class EnvironmentErrorA(AnimicaError):
    def __init__(self, message="bad environment", **data: Any) -> None:
        super().__init__(
            code=CoreErrorCode.ENVIRONMENT,
            message=message,
            data=_jsonmap(data),
            retryable=False,
        )


class SerializationError(AnimicaError):
    def __init__(self, message="serialization failed", **data: Any) -> None:
        super().__init__(
            code=CoreErrorCode.SERIALIZATION, message=message, data=_jsonmap(data)
        )


class DeserializationError(AnimicaError):
    def __init__(self, message="deserialization failed", **data: Any) -> None:
        super().__init__(
            code=CoreErrorCode.DESERIALIZATION, message=message, data=_jsonmap(data)
        )


class CDDLValidationError(AnimicaError):
    def __init__(self, message="CDDL validation error", **data: Any) -> None:
        super().__init__(
            code=CoreErrorCode.CDDL_VALIDATION,
            message=message,
            data=_jsonmap(data),
            retryable=False,
        )


class SchemaValidationError(AnimicaError):
    def __init__(self, message="schema validation error", **data: Any) -> None:
        super().__init__(
            code=CoreErrorCode.SCHEMA_VALIDATION,
            message=message,
            data=_jsonmap(data),
            retryable=False,
        )


class HashMismatch(AnimicaError):
    def __init__(self, expected: str, got: str, subject: str = "payload") -> None:
        super().__init__(
            code=CoreErrorCode.HASH_MISMATCH,
            message=f"hash mismatch for {subject}",
            data={"expected": expected, "got": got, "subject": subject},
            retryable=False,
        )


class DatabaseError(AnimicaError):
    def __init__(
        self, message="database error", retryable: bool = True, **data: Any
    ) -> None:
        super().__init__(
            code=CoreErrorCode.DB,
            message=message,
            data=_jsonmap(data),
            retryable=retryable,
        )


class NotFound(DatabaseError):
    def __init__(self, key: str, space: str = "kv") -> None:
        super().__init__(message="not found", retryable=False, key=key, space=space)
        self.code = CoreErrorCode.DB_NOT_FOUND  # override


class AlreadyExists(DatabaseError):
    def __init__(self, key: str, space: str = "kv") -> None:
        super().__init__(
            message="already exists", retryable=False, key=key, space=space
        )
        self.code = CoreErrorCode.DB_CONFLICT


class GenesisError(AnimicaError):
    def __init__(self, message="invalid genesis", **data: Any) -> None:
        super().__init__(
            code=CoreErrorCode.GENESIS,
            message=message,
            data=_jsonmap(data),
            retryable=False,
        )


class ChainIdMismatch(AnimicaError):
    def __init__(self, expected: int | str, got: int | str) -> None:
        super().__init__(
            code=CoreErrorCode.CHAIN_ID_MISMATCH,
            message="chain id mismatch",
            data={"expected": expected, "got": got},
            retryable=False,
        )


class HeaderInvalid(AnimicaError):
    def __init__(self, message="invalid header", **data: Any) -> None:
        super().__init__(
            code=CoreErrorCode.HEADER_INVALID,
            message=message,
            data=_jsonmap(data),
            retryable=False,
        )


class BlockInvalid(AnimicaError):
    def __init__(self, message="invalid block", **data: Any) -> None:
        super().__init__(
            code=CoreErrorCode.BLOCK_INVALID,
            message=message,
            data=_jsonmap(data),
            retryable=False,
        )


class StateInvariant(AnimicaError):
    def __init__(self, message="state invariant broken", **data: Any) -> None:
        super().__init__(
            code=CoreErrorCode.STATE_INVARIANT,
            message=message,
            data=_jsonmap(data),
            retryable=False,
        )


class TxInvalid(AnimicaError):
    def __init__(self, message="invalid transaction", **data: Any) -> None:
        super().__init__(
            code=CoreErrorCode.TX_INVALID,
            message=message,
            data=_jsonmap(data),
            retryable=False,
        )


class TxNonceGap(TxInvalid):
    def __init__(self, sender: str, expected: int, got: int) -> None:
        super().__init__(message="nonce gap", sender=sender, expected=expected, got=got)
        self.code = CoreErrorCode.TX_NONCE_GAP


class TxInsufficientBalance(TxInvalid):
    def __init__(self, sender: str, needed: int, balance: int) -> None:
        super().__init__(
            message="insufficient balance",
            sender=sender,
            needed=needed,
            balance=balance,
        )
        self.code = CoreErrorCode.TX_INSUFFICIENT_BAL


class TxOutOfGas(TxInvalid):
    def __init__(self, gas_limit: int, gas_used: int) -> None:
        super().__init__(message="out of gas", gas_limit=gas_limit, gas_used=gas_used)
        self.code = CoreErrorCode.TX_OOG


class IOErrorA(AnimicaError):
    def __init__(
        self, message="I/O error", retryable: bool = True, **data: Any
    ) -> None:
        super().__init__(
            code=CoreErrorCode.IO,
            message=message,
            data=_jsonmap(data),
            retryable=retryable,
        )


class NetworkError(AnimicaError):
    def __init__(
        self, message="network error", retryable: bool = True, **data: Any
    ) -> None:
        super().__init__(
            code=CoreErrorCode.NETWORK,
            message=message,
            data=_jsonmap(data),
            retryable=retryable,
        )


class ResourceExhausted(AnimicaError):
    def __init__(self, resource: str, limit: int | None = None, **data: Any) -> None:
        d = {"resource": resource, **data}
        if limit is not None:
            d["limit"] = limit
        super().__init__(
            code=CoreErrorCode.RESOURCE_EXHAUSTED,
            message=f"resource exhausted: {resource}",
            data=_jsonmap(d),
            retryable=True,
        )


class TimeoutErrorA(AnimicaError):
    def __init__(self, message="operation timed out", **data: Any) -> None:
        super().__init__(
            code=CoreErrorCode.TIMEOUT,
            message=message,
            data=_jsonmap(data),
            retryable=True,
        )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

T = TypeVar("T", bound=AnimicaError)


def wrap(exc: BaseException, *, as_: Type[T] = InternalError, **ctx: Any) -> T:
    """
    Wrap any exception into an AnimicaError subclass, attaching context.
    If `exc` is already an AnimicaError, returns a context-enriched copy.
    """
    if isinstance(exc, AnimicaError):
        return exc.with_context(**ctx)  # type: ignore[return-value]
    err = as_("wrapped exception", **ctx)  # type: ignore[call-arg]
    return err.with_cause(exc)


def ensure_animica_error(exc: BaseException) -> AnimicaError:
    """Coerce unknown exceptions to InternalError with cause attached."""
    return exc if isinstance(exc, AnimicaError) else InternalError().with_cause(exc)


def _jsonmap(data: Mapping[str, Any]) -> Dict[str, Any]:
    return {k: _coerce_json(v) for k, v in data.items()}


def _coerce_json(v: Any) -> Any:
    # Keep JSON primitives; stringify the rest; hex-encode bytes.
    if v is None or isinstance(v, (bool, int, float, str, list, dict)):
        return v
    if isinstance(v, (bytes, bytearray)):
        return v.hex()
    try:
        return str(v)
    except Exception:
        return "<unprintable>"


def _preview(v: Any, limit: int = 96) -> str:
    s = _coerce_json(v)
    s = str(s)
    return s if len(s) <= limit else s[:limit] + "…"


# ---------------------------------------------------------------------------
# Minimal mapping to HTTP / RPC (opt-in)
# ---------------------------------------------------------------------------

HTTP_MAP = {
    CoreErrorCode.INTERNAL: 500,
    CoreErrorCode.NOT_IMPLEMENTED: 501,
    CoreErrorCode.DEP_MISSING: 500,
    CoreErrorCode.FEATURE_DISABLED: 403,
    CoreErrorCode.RESOURCE_EXHAUSTED: 429,
    CoreErrorCode.TIMEOUT: 504,
    CoreErrorCode.CONFIG: 400,
    CoreErrorCode.ENVIRONMENT: 500,
    CoreErrorCode.SERIALIZATION: 500,
    CoreErrorCode.DESERIALIZATION: 400,
    CoreErrorCode.CDDL_VALIDATION: 400,
    CoreErrorCode.SCHEMA_VALIDATION: 400,
    CoreErrorCode.HASH_MISMATCH: 400,
    CoreErrorCode.DB: 500,
    CoreErrorCode.DB_NOT_FOUND: 404,
    CoreErrorCode.DB_CONFLICT: 409,
    CoreErrorCode.GENESIS: 400,
    CoreErrorCode.CHAIN_ID_MISMATCH: 409,
    CoreErrorCode.HEADER_INVALID: 400,
    CoreErrorCode.BLOCK_INVALID: 400,
    CoreErrorCode.STATE_INVARIANT: 500,
    CoreErrorCode.TX_INVALID: 400,
    CoreErrorCode.TX_NONCE_GAP: 400,
    CoreErrorCode.TX_INSUFFICIENT_BAL: 400,
    CoreErrorCode.TX_OOG: 400,
    CoreErrorCode.IO: 500,
    CoreErrorCode.NETWORK: 502,
}


def http_status_for(err: AnimicaError) -> int:
    """Best-effort HTTP status mapping for bridges (RPC/REST)."""
    return HTTP_MAP.get(CoreErrorCode(err.code), 500) if err.code in CoreErrorCode.__members__.values() else 500  # type: ignore[arg-type]
