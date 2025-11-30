from __future__ import annotations

"""
stdlib.abi — minimal, deterministic ABI helpers for contracts in the browser simulator.

What this provides
------------------
- Control flow:
    - revert(msg=b"...")
    - require(cond, msg=b"...")
- Canonical scalar encoders/decoders used in examples and tests:
    - enc_bool / dec_bool
    - enc_u256 / dec_u256
    - enc_bytes (identity)
- Simple byte-level packing helpers for argument/return payloads:
    - encode_args(values) → bytes
    - decode_args(blob)  → list[bytes]
    - encode_return(value) / decode_return(blob, as_="bytes")

Notes
-----
* All functions are pure/deterministic and validate inputs strictly.
* Integer encoding is 32-byte big-endian unsigned (u256).
* Boolean encoding is a single byte: 0x00 or 0x01.
* Strings are NOT implicitly supported here to avoid locale/round-trip issues.
  If you must use strings, encode to UTF-8 bytes yourself at the call site.

Binary layout for encode_args
-----------------------------
blob := DOM(=b"animica|abi|v1") || u32be(count) || Σ (u32be(len_i) || val_i)

This matches the simulator's runtime ABI expectations used by the editor/preview.
"""

from typing import Any, Iterable, List, Tuple

from ..errors import Revert as _Revert
from ..errors import ValidationError

# ---------------- Control flow ----------------


def revert(msg: bytes | bytearray = b"") -> None:
    """
    Abort execution with a deterministic Revert. The simulator will surface the
    revert bytes as the reason where applicable.
    """
    if not isinstance(msg, (bytes, bytearray)):
        raise ValidationError("revert message must be bytes")
    raise _Revert(bytes(msg))


def require(cond: bool, msg: bytes | bytearray = b"") -> None:
    """
    Revert with `msg` if `cond` is False.
    """
    if not isinstance(cond, bool):
        raise ValidationError("require() expects a boolean condition")
    if not cond:
        revert(msg)


# ---------------- Scalar encoders/decoders ----------------


def enc_bool(v: bool) -> bytes:
    if not isinstance(v, bool):
        raise ValidationError("enc_bool expects a bool")
    return b"\x01" if v else b"\x00"


def dec_bool(b: bytes | bytearray) -> bool:
    if not isinstance(b, (bytes, bytearray)) or len(b) != 1:
        raise ValidationError("dec_bool expects exactly 1 byte")
    if b[0] == 0:
        return False
    if b[0] == 1:
        return True
    raise ValidationError("dec_bool requires 0x00 or 0x01")


_U256_MAX = (1 << 256) - 1


def enc_u256(n: int) -> bytes:
    if not isinstance(n, int) or n < 0:
        raise ValidationError("enc_u256 expects a non-negative int")
    if n > _U256_MAX:
        raise ValidationError("enc_u256 overflow (> 2^256-1)")
    return n.to_bytes(32, "big")


def dec_u256(b: bytes | bytearray) -> int:
    if not isinstance(b, (bytes, bytearray)) or len(b) != 32:
        raise ValidationError("dec_u256 expects exactly 32 bytes")
    out = 0
    for x in b:
        out = (out << 8) | x
    return out


def enc_bytes(v: bytes | bytearray) -> bytes:
    if not isinstance(v, (bytes, bytearray)):
        raise ValidationError("enc_bytes expects bytes")
    return bytes(v)


# ---------------- Packing helpers ----------------

_DOM = b"animica|abi|v1"


def _u32be(n: int) -> bytes:
    if not isinstance(n, int) or n < 0 or n > 0xFFFFFFFF:
        raise ValidationError("length out of range for u32")
    return n.to_bytes(4, "big")


def _read_u32be(data: bytes, off: int) -> Tuple[int, int]:
    if off + 4 > len(data):
        raise ValidationError("truncated u32 in ABI blob")
    return int.from_bytes(data[off : off + 4], "big"), off + 4


def _as_bytes(v: Any) -> bytes:
    if isinstance(v, (bytes, bytearray)):
        return bytes(v)
    if isinstance(v, bool):
        return enc_bool(v)
    if isinstance(v, int):
        return enc_u256(v)
    raise ValidationError(f"unsupported value type for ABI encode: {type(v).__name__}")


def encode_args(values: Iterable[Any]) -> bytes:
    """
    Encode an iterable of values (bytes|bool|int) into a canonical blob.

    Strings are intentionally not supported to avoid implicit UTF-8 choices.
    Convert to bytes explicitly first if needed.
    """
    parts: List[bytes] = [_as_bytes(v) for v in values]
    out = bytearray(_DOM)
    out += _u32be(len(parts))
    for p in parts:
        out += _u32be(len(p))
        out += p
    return bytes(out)


def decode_args(blob: bytes | bytearray) -> List[bytes]:
    """
    Decode a blob produced by `encode_args` and return the list of raw byte parts.
    """
    if not isinstance(blob, (bytes, bytearray)):
        raise ValidationError("decode_args expects bytes")
    data = bytes(blob)
    if not data.startswith(_DOM):
        raise ValidationError("ABI blob missing domain header")
    off = len(_DOM)
    count, off = _read_u32be(data, off)
    parts: List[bytes] = []
    for _ in range(count):
        ln, off = _read_u32be(data, off)
        if off + ln > len(data):
            raise ValidationError("truncated part in ABI blob")
        parts.append(data[off : off + ln])
        off += ln
    if off != len(data):
        raise ValidationError("extra trailing bytes in ABI blob")
    return parts


def encode_return(value: Any) -> bytes:
    """
    Encode a single value (bytes|bool|int) as a return blob (same layout as args
    but with a single element).
    """
    return encode_args([value])


def decode_return(blob: bytes | bytearray, as_: str = "bytes") -> Any:
    """
    Decode a single-value return blob and coerce to the requested type:
      - "bytes" (default) → bytes
      - "bool"           → bool
      - "u256"           → int
    """
    parts = decode_args(blob)
    if len(parts) != 1:
        raise ValidationError("return blob must contain exactly one value")
    b = parts[0]
    if as_ == "bytes":
        return b
    if as_ == "bool":
        return dec_bool(b)
    if as_ == "u256":
        return dec_u256(b)
    raise ValidationError(f"unsupported as_ type for decode_return: {as_!r}")


__all__ = [
    "revert",
    "require",
    "enc_bool",
    "dec_bool",
    "enc_u256",
    "dec_u256",
    "enc_bytes",
    "encode_args",
    "decode_args",
    "encode_return",
    "decode_return",
]
