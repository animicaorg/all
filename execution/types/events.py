"""
execution.types.events — event/log record types for Animica execution.

`LogEvent` is a compact, deterministic container used by the execution engine to
record contract-emitted events. It is intentionally minimal and dependency-free.

Conventions
-----------
* `address` is stored as raw bytes (typically 20 or 32 bytes depending on the chain's
  address scheme). Higher layers may render it as bech32m or hex.
* `topics` are an ordered tuple of fixed-length bytes (commonly 32 bytes each).
* `data` is an arbitrary byte string payload.

Helpers
-------
* `to_dict()` / `from_dict()` convert to/from JSON-friendly hex forms.
* Inputs may be provided as hex strings (with or without 0x) and are normalized.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Sequence, Tuple, Union

HexLike = Union[str, bytes, bytearray, memoryview]


def _is_hex_str(s: str) -> bool:
    s2 = s[2:] if s.startswith(("0x", "0X")) else s
    if len(s2) % 2:
        return False
    try:
        bytes.fromhex(s2)
        return True
    except ValueError:
        return False


def _hex_to_bytes(v: HexLike) -> bytes:
    if isinstance(v, (bytes, bytearray, memoryview)):
        return bytes(v)
    if isinstance(v, str):
        s = v.strip()
        if s.startswith(("0x", "0X")):
            s = s[2:]
        if len(s) % 2:
            # tolerate odd-length hex by prefixing a zero nibble
            s = "0" + s
        try:
            return bytes.fromhex(s)
        except ValueError as e:
            raise ValueError(f"invalid hex string: {v!r}") from e
    raise TypeError(f"expected hex-like value, got {type(v).__name__}")


def _bytes_to_hex(b: bytes) -> str:
    return "0x" + b.hex()


def _normalize_topic(t: HexLike) -> bytes:
    b = _hex_to_bytes(t)
    if len(b) == 0:
        raise ValueError("topic must not be empty")
    # Common case is 32 bytes; do not *require* it to allow future-proofing.
    return b


@dataclass(frozen=True)
class LogEvent:
    """
    A single event/log emitted during transaction execution.

    Attributes:
        address: bytes — emitter address (20 or 32 bytes typical)
        topics:  tuple[bytes, ...] — ordered topics (32B typical)
        data:    bytes — unstructured payload

    Raises:
        ValueError if inputs are empty or obviously malformed.
    """

    address: bytes
    topics: Tuple[bytes, ...]
    data: bytes

    # --------------------- construction helpers ---------------------

    def __init__(
        self, address: HexLike, topics: Sequence[HexLike] = (), data: HexLike = b""
    ):
        addr_b = _hex_to_bytes(address)
        if len(addr_b) == 0:
            raise ValueError("address must not be empty")
        if len(topics) == 0:
            # zero topics is permitted, but keep tuple invariant
            topics_b: Tuple[bytes, ...] = tuple()
        else:
            topics_b = tuple(_normalize_topic(t) for t in topics)
        data_b = (
            _hex_to_bytes(data)
            if isinstance(data, str) or isinstance(data, (bytes, bytearray, memoryview))
            else bytes(data)
        )

        # Basic sanity checks without enforcing a single address size.
        if len(addr_b) not in (20, 24, 32):
            # Allow other sizes but discourage footguns.
            # We keep it a warning-level semantics by raising only if clearly absurd.
            if len(addr_b) < 8:
                raise ValueError(f"address length too small: {len(addr_b)} bytes")

        object.__setattr__(self, "address", addr_b)
        object.__setattr__(self, "topics", topics_b)
        object.__setattr__(self, "data", data_b)

    # --------------------- conversions & representations ---------------------

    def to_dict(self) -> Dict[str, Any]:
        """
        Serialize to a JSON-friendly mapping using hex strings.

        Returns:
            {
              "address": "0x..",
              "topics":  ["0x..", ...],
              "data":    "0x.."
            }
        """
        return {
            "address": _bytes_to_hex(self.address),
            "topics": [_bytes_to_hex(t) for t in self.topics],
            "data": _bytes_to_hex(self.data),
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "LogEvent":
        """
        Parse from a mapping that may contain hex strings or byte-like values.
        """
        addr = d.get("address")
        topics = d.get("topics", [])
        data = d.get("data", b"")
        if isinstance(topics, (tuple, list)):
            topics_seq = list(topics)
        else:
            raise TypeError("topics must be a list/tuple")
        return cls(address=addr, topics=topics_seq, data=data)

    # Pretty printable (short) representation
    def __repr__(self) -> str:  # pragma: no cover - cosmetic
        addr = _bytes_to_hex(self.address)
        ts = ", ".join(
            _bytes_to_hex(t)[:12] + "…" if len(t) > 6 else _bytes_to_hex(t)
            for t in self.topics
        )
        data_h = _bytes_to_hex(self.data)
        if len(data_h) > 18:
            data_h = data_h[:18] + "…"
        return f"LogEvent(address={addr[:12]}…, topics=[{ts}], data={data_h})"


__all__ = ["LogEvent"]
