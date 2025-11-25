"""
Animica P2P wire encoding utilities.

This module provides:
  • Deterministic serialization for payloads (CBOR canonical by default).
  • Optional msgpack via `msgspec` with recursive canonical key ordering.
  • Compact checksums (SHA3-256 truncated) for frame/body integrity.
  • Safe size guards and varint helpers (shared by p2p.wire.frames).

Notes
-----
- CBOR encoding delegates to core.encoding.cbor which implements RFC 7049
  canonical ordering (major type ordering, length-first sorting, etc.).
- msgpack (msgspec) is supported as an opt-in wire format for experiments,
  but CBOR is the network default for interoperability with specs and tools.
"""

from __future__ import annotations

from dataclasses import is_dataclass, asdict
from enum import IntEnum
from typing import Any, Dict, Iterable, Mapping, MutableMapping, Optional, Tuple, Union

# Canonical CBOR used across the project
from core.encoding.cbor import dumps as cbor_dumps, loads as cbor_loads  # type: ignore
from core.utils.hash import sha3_256  # type: ignore

try:  # Optional acceleration / struct typing
    import msgspec  # type: ignore
    _HAVE_MSGSPEC = True
except Exception:  # pragma: no cover - optional
    msgspec = None
    _HAVE_MSGSPEC = False


# ------------------------------------------
# Limits & constants
# ------------------------------------------

# Hard limit to protect allocs; frames layer also enforces its own ceilings.
MAX_PAYLOAD_BYTES: int = 8 * 1024 * 1024  # 8 MiB (tunable)
CHECKSUM_BYTES: int = 16  # SHA3-256 truncated to 128 bits (collision-resistant for framing)
DEFAULT_WIRE: "WireFormat" = None  # set below after WireFormat is defined


class EncodingError(Exception):
    """Wire encoding/decoding failure."""


class WireFormat(IntEnum):
    CBOR = 0
    MSGPACK = 1

    @classmethod
    def from_name(cls, name: str) -> "WireFormat":
        n = name.strip().lower()
        if n in ("cbor", "cbor_deterministic"):
            return cls.CBOR
        if n in ("msgpack", "msgspec"):
            return cls.MSGPACK
        raise ValueError(f"unknown wire format: {name}")

    def __str__(self) -> str:  # pragma: no cover - trivial
        return "cbor" if self is WireFormat.CBOR else "msgpack"


DEFAULT_WIRE = WireFormat.CBOR


# ------------------------------------------
# Canonicalization helpers
# ------------------------------------------

JSONLike = Union[Mapping[str, Any], MutableMapping[str, Any], list, tuple, str, int, float, bool, None]


def _dataclass_to_plain(obj: Any) -> Any:
    """Convert dataclass → dict recursively (without losing field order)."""
    if is_dataclass(obj):
        obj = asdict(obj)
    if isinstance(obj, Mapping):
        return {k: _dataclass_to_plain(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        t = type(obj)
        return t(_dataclass_to_plain(v) for v in obj)
    return obj


def canonicalize(obj: JSONLike) -> JSONLike:
    """
    Recursively sort mapping keys lexicographically and canonicalize contained values.

    This is used primarily for msgpack, which has no intrinsic canonical-map rule.
    For CBOR we rely on core.encoding.cbor to enforce RFC-canonical ordering.
    """
    if isinstance(obj, Mapping):
        # Ensure keys are strings; if not, normalize to strings deterministically.
        # (CBOR can encode non-string keys but our schemas restrict to strings.)
        items = []
        for k, v in obj.items():
            if not isinstance(k, str):
                k = str(k)
            items.append((k, canonicalize(v)))
        items.sort(key=lambda kv: kv[0])
        return {k: v for k, v in items}
    if isinstance(obj, (list, tuple)):
        t = type(obj)
        return t(canonicalize(v) for v in obj)
    return obj


# ------------------------------------------
# Varint (u64) helpers (LEB128-like)
# ------------------------------------------

def varu64_encode(n: int) -> bytes:
    """Unsigned LEB128 encoding for non-negative integers."""
    if n < 0:
        raise EncodingError("varu64 cannot encode negative integers")
    out = bytearray()
    while True:
        b = n & 0x7F
        n >>= 7
        if n:
            out.append(b | 0x80)
        else:
            out.append(b)
            break
    return bytes(out)


def varu64_decode(data: bytes, *, offset: int = 0) -> Tuple[int, int]:
    """Decode unsigned LEB128 from data[offset:], returns (value, next_offset)."""
    shift = 0
    value = 0
    i = offset
    while True:
        if i >= len(data):
            raise EncodingError("incomplete varu64")
        b = data[i]
        i += 1
        value |= (b & 0x7F) << shift
        if (b & 0x80) == 0:
            break
        shift += 7
        if shift > 63:
            raise EncodingError("varu64 too large")
    return value, i


# ------------------------------------------
# Checksums
# ------------------------------------------

def checksum(data: bytes, *, size: int = CHECKSUM_BYTES) -> bytes:
    """
    Compute truncated SHA3-256 checksum.

    Rationale: fast on modern CPUs, strong preimage/collision properties.
    We truncate to 16 bytes for compact framing; tune via `size` if needed.
    """
    if size <= 0 or size > 32:
        raise ValueError("checksum size must be in [1, 32]")
    return sha3_256(data)[:size]


def verify_checksum(data: bytes, digest: bytes) -> bool:
    """Constant-time-ish equality check of checksum(data) == digest."""
    c = checksum(data, size=len(digest))
    # Constant-time compare
    if len(c) != len(digest):
        return False
    acc = 0
    for x, y in zip(c, digest):
        acc |= (x ^ y)
    return acc == 0


# ------------------------------------------
# Encoding / decoding
# ------------------------------------------

def encode_payload(obj: Any, *, fmt: WireFormat = DEFAULT_WIRE, max_bytes: int = MAX_PAYLOAD_BYTES) -> bytes:
    """
    Encode a Python object to wire bytes using the selected format.

    - Dataclasses are converted to plain dict/list structures first.
    - For msgpack, maps are canonicalized (sorted keys) to ensure determinism.
    """
    obj = _dataclass_to_plain(obj)

    if fmt is WireFormat.CBOR:
        data = cbor_dumps(obj)
    elif fmt is WireFormat.MSGPACK:
        if not _HAVE_MSGSPEC:
            raise EncodingError("msgspec is not available; cannot encode msgpack")
        normalized = canonicalize(obj)
        data = msgspec.msgpack.encode(normalized)  # type: ignore[attr-defined]
    else:  # pragma: no cover - defensive
        raise EncodingError(f"unsupported wire format: {fmt}")

    if len(data) > max_bytes:
        raise EncodingError(f"payload too large: {len(data)} > {max_bytes}")

    return data


def decode_payload(
    data: bytes,
    *,
    fmt: WireFormat = DEFAULT_WIRE,
    typehint: Optional[Any] = None,
    max_bytes: int = MAX_PAYLOAD_BYTES,
) -> Any:
    """
    Decode wire bytes to Python object using selected format.

    - `typehint` can be a msgspec.Struct/dataclass/typing object for msgspec fast path.
    - For CBOR we ignore typehint and return plain Python types (mapping/list/scalars).
    """
    if len(data) > max_bytes:
        raise EncodingError(f"payload too large: {len(data)} > {max_bytes}")

    if fmt is WireFormat.CBOR:
        return cbor_loads(data)
    elif fmt is WireFormat.MSGPACK:
        if not _HAVE_MSGSPEC:
            raise EncodingError("msgspec is not available; cannot decode msgpack")
        decoder = msgspec.msgpack.Decoder(type=typehint) if typehint is not None else msgspec.msgpack.Decoder()  # type: ignore[attr-defined]
        return decoder.decode(data)
    else:  # pragma: no cover - defensive
        raise EncodingError(f"unsupported wire format: {fmt}")


def encode_with_checksum(
    obj: Any,
    *,
    fmt: WireFormat = DEFAULT_WIRE,
    max_bytes: int = MAX_PAYLOAD_BYTES,
    cksum_size: int = CHECKSUM_BYTES,
) -> Tuple[bytes, bytes]:
    """
    Convenience: encode → checksum in one go.

    Returns (payload_bytes, checksum_bytes).
    """
    payload = encode_payload(obj, fmt=fmt, max_bytes=max_bytes)
    return payload, checksum(payload, size=cksum_size)


def decode_with_checksum(
    payload: bytes,
    cksum: bytes,
    *,
    fmt: WireFormat = DEFAULT_WIRE,
    typehint: Optional[Any] = None,
    max_bytes: int = MAX_PAYLOAD_BYTES,
) -> Any:
    """
    Verify checksum then decode payload → object.
    Raises EncodingError on checksum mismatch.
    """
    if not verify_checksum(payload, cksum):
        raise EncodingError("checksum mismatch")
    return decode_payload(payload, fmt=fmt, typehint=typehint, max_bytes=max_bytes)


# ------------------------------------------
# Length-prefixing helpers (used by stream readers)
# ------------------------------------------

def prefix_length(data: bytes) -> bytes:
    """Return varu64 length prefix + data."""
    return varu64_encode(len(data)) + data


def strip_length_prefixed(data: bytes, *, offset: int = 0) -> Tuple[bytes, int]:
    """
    Parse varu64 length-prefixed blob starting at `offset`.
    Returns (payload_bytes, next_offset).
    """
    length, i = varu64_decode(data, offset=offset)
    j = i + length
    if length < 0 or j > len(data):
        raise EncodingError("invalid length-prefixed payload")
    return data[i:j], j


# ------------------------------------------
# Public surface
# ------------------------------------------

__all__ = [
    "WireFormat",
    "DEFAULT_WIRE",
    "MAX_PAYLOAD_BYTES",
    "CHECKSUM_BYTES",
    "EncodingError",
    "canonicalize",
    "varu64_encode",
    "varu64_decode",
    "checksum",
    "verify_checksum",
    "encode_payload",
    "decode_payload",
    "encode_with_checksum",
    "decode_with_checksum",
    "prefix_length",
    "strip_length_prefixed",
]
