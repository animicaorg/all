"""Data models for JSON-RPC responses from an Animica node."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Generic, TypeVar

T = TypeVar("T")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def parse_hex_quantity(value: Any, name: str = "quantity") -> int:
    """Parse *value* as a hex-encoded or plain integer quantity.

    Accepts:
    * ``"0x..."`` hex strings
    * Plain integers

    Raises
    ------
    ValueError
        If *value* cannot be interpreted as an integer.
    """
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        s = value.strip()
        if s.startswith("0x") or s.startswith("0X"):
            try:
                return int(s, 16)
            except ValueError:
                pass
        # Try plain decimal string
        try:
            return int(s, 10)
        except ValueError:
            pass
    raise ValueError(f"Cannot parse {name} from {value!r}")


def validate_hash(value: Any) -> str:
    """Validate and return a 0x-prefixed 32-byte (64 hex char) hash string.

    Raises
    ------
    ValueError
        If *value* is not a valid hash.
    """
    if not isinstance(value, str):
        raise ValueError(f"Expected str hash, got {type(value).__name__}")
    s = value.strip()
    if not (s.startswith("0x") or s.startswith("0X")):
        raise ValueError(f"Hash must start with 0x: {s!r}")
    hex_part = s[2:]
    if len(hex_part) != 64:
        raise ValueError(f"Hash must be 32 bytes (64 hex chars), got {len(hex_part)}: {s!r}")
    try:
        int(hex_part, 16)
    except ValueError:
        raise ValueError(f"Hash contains non-hex characters: {s!r}")
    return s


# ---------------------------------------------------------------------------
# Error / Response envelopes
# ---------------------------------------------------------------------------


@dataclass
class RpcError:
    """JSON-RPC 2.0 error object."""

    code: int
    message: str
    data: Any = None

    def __str__(self) -> str:
        base = f"RpcError({self.code}): {self.message}"
        if self.data is not None:
            base += f" | data={self.data!r}"
        return base


@dataclass
class RpcResponse(Generic[T]):
    """Parsed JSON-RPC 2.0 response envelope."""

    id: int | str | None
    result: T | None = None
    error: RpcError | None = None
    raw: dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Domain models
# ---------------------------------------------------------------------------


@dataclass
class Head:
    """Minimal chain head representation."""

    number: int
    hash: str
    timestamp: int | None = None
    extra: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Head":
        """Create a :class:`Head` from a raw RPC result dict."""
        number_raw = d.get("number", 0)
        number = parse_hex_quantity(number_raw, "number")

        hash_raw = d.get("hash", "")
        try:
            hash_val = validate_hash(hash_raw)
        except ValueError:
            hash_val = str(hash_raw)

        ts_raw = d.get("timestamp")
        timestamp: int | None = None
        if ts_raw is not None:
            try:
                timestamp = parse_hex_quantity(ts_raw, "timestamp")
            except ValueError:
                pass

        extra = {k: v for k, v in d.items() if k not in ("number", "hash", "timestamp")}
        # Also check nested header
        if "header" in extra and isinstance(extra["header"], dict):
            h = extra["header"]
            if timestamp is None and "timestamp" in h:
                try:
                    timestamp = parse_hex_quantity(h["timestamp"], "timestamp")
                except ValueError:
                    pass

        return cls(number=number, hash=hash_val, timestamp=timestamp, extra=extra)


@dataclass
class BalanceResponse:
    """Parsed balance from an RPC call."""

    quantity: int

    @classmethod
    def from_raw(cls, raw: Any) -> "BalanceResponse":
        """Create a :class:`BalanceResponse` from a raw RPC result value."""
        qty = parse_hex_quantity(raw, "balance")
        return cls(quantity=qty)
