from __future__ import annotations

"""
stdlib.events — deterministic event emit helpers for the browser simulator.

This wraps the runtime events API with a small, strict surface suitable for
contracts. It validates names/keys and encodes values canonically so logs are
stable across platforms.

Encoding rules
--------------
- Event name: bytes (or str → UTF-8), 1..64 bytes.
- Arg keys: bytes (or str → UTF-8), 1..64 bytes.
- Arg values:
    * bytes/bytearray: used as-is (copied)
    * str: UTF-8 bytes
    * bool: b"\\x01" or b"\\x00"
    * int (>=0): 32-byte big-endian unsigned (u256)
  Any other type raises ValidationError.

Example
-------
    from stdlib import events

    events.emit(b"Inc", {"by": 1})
    events.emit("Transfer", {"from": addr_from, "to": addr_to, "amount": 100})

The runtime captures events in-order with the encoded (name, {k: v}) payload.
"""

from typing import Any, Dict, Mapping, Tuple

from ..errors import ValidationError
from ..runtime import events_api

_MAX_NAME_LEN = 64
_MAX_KEY_LEN = 64


# ---------------- Internal helpers ----------------


def _to_bytes(name: str, v: Any) -> bytes:
    if isinstance(v, bytes):
        return v
    if isinstance(v, bytearray):
        return bytes(v)
    if isinstance(v, str):
        return v.encode("utf-8")
    raise ValidationError(f"{name} must be bytes or str")


def _check_name(name: bytes) -> bytes:
    if not isinstance(name, (bytes, bytearray)):
        raise ValidationError("event name must be bytes")
    b = bytes(name)
    if len(b) == 0:
        raise ValidationError("event name must be non-empty")
    if len(b) > _MAX_NAME_LEN:
        raise ValidationError("event name too long")
    return b


def _check_key(k: bytes) -> bytes:
    if not isinstance(k, (bytes, bytearray)):
        raise ValidationError("arg key must be bytes")
    b = bytes(k)
    if len(b) == 0:
        raise ValidationError("arg key must be non-empty")
    if len(b) > _MAX_KEY_LEN:
        raise ValidationError("arg key too long")
    return b


def _encode_u256(n: int) -> bytes:
    if not isinstance(n, int) or n < 0:
        raise ValidationError("integer event value must be a non-negative int")
    if n.bit_length() > 256:
        raise ValidationError("integer event value exceeds 256 bits")
    return n.to_bytes(32, "big")


def _encode_value(v: Any) -> bytes:
    if isinstance(v, (bytes, bytearray)):
        return bytes(v)
    if isinstance(v, str):
        return v.encode("utf-8")
    if isinstance(v, bool):
        return b"\x01" if v else b"\x00"
    if isinstance(v, int):
        return _encode_u256(v)
    raise ValidationError(f"unsupported event value type: {type(v).__name__}")


def _normalize_args(args: Mapping[Any, Any]) -> Dict[bytes, bytes]:
    out: Dict[bytes, bytes] = {}
    for k, v in args.items():
        kb = _to_bytes("arg key", k)
        kb = _check_key(kb)
        vb = _encode_value(v)
        out[kb] = vb
    return out


# ---------------- Public API ----------------


def emit(name: bytes | str, args: Mapping[Any, Any] | None = None) -> None:
    """
    Emit a deterministic event.

    :param name: bytes or str (UTF-8), length 1..64
    :param args: mapping of (key -> value), where keys are bytes/str and
                 values follow the encoding rules above.
    """
    nb = _to_bytes("event name", name)
    nb = _check_name(nb)
    enc = _normalize_args(args or {})
    events_api.emit(nb, enc)


__all__ = ["emit"]
