from __future__ import annotations

"""
Helpers to (de)serialize ABI payloads for the Pyodide bridge.

Goals
-----
- Accept browser/JSON-friendly values and normalize them to the VM's ABI
  expectations (e.g., hex strings -> bytes, nested structures handled).
- Convert VM results back to JSON-serializable forms (bytes -> 0x-hex).
- Provide light-weight address parsing without heavy deps.

Design
------
We avoid importing the full SDK or external codecs to keep the WASM
payload tiny. Conventions:

- Bytes:
    * Input: "0x..." hex string (even length) or Python bytes/bytearray
    * Output: "0x" + hex (lowercase)

- Address (20 bytes typical):
    * Input: bytes of length 20, or "0x" + 40 hex chars
    * Output: "0x" + 40 hex chars
    * Bech32 is intentionally not supported here to keep deps minimal.

- Integers:
    * JSON numbers are accepted as Python int.
    * Range checks are *not* enforced here; leave it to the VM layer.

- Lists / dicts:
    * Traversed recursively, applying the above rules to leaf values.

These helpers are intentionally generic; if you have an ABI schema handy,
you can add shape-specific validation upstream before calling normalize.
"""

from typing import (Any, Dict, Iterable, List, Mapping, MutableMapping,
                    Sequence, Tuple, Union)

# ----------------------------- Public API -----------------------------


def normalize_args(args: Sequence[Any]) -> List[Any]:
    """
    Convert a sequence of JSON-friendly arguments to ABI-friendly Python values.

    Rules:
      - "0x..." -> bytes
      - {"__bytes": "0x..."} -> bytes
      - lists/tuples -> recursively normalized lists
      - dict -> recursively normalized dict with same keys
    """
    return [_normalize_value(a) for a in args]


def normalize_value(value: Any) -> Any:
    """
    Public single-value variant of normalize_args (exposed for convenience).
    """
    return _normalize_value(value)


def denormalize_result(value: Any) -> Any:
    """
    Convert VM results to JSON-safe values:
      - bytes -> "0x..."
      - lists/tuples -> recursively converted lists
      - dict -> recursively converted dict (keeps keys as-is)
    """
    return _denormalize_value(value)


def parse_address(value: Any, *, length: int = 20) -> bytes:
    """
    Parse an address value. Accepts:
      - bytes/bytearray of expected length
      - "0x" + 2*length hex string

    Raises ValueError on invalid input.
    """
    if isinstance(value, (bytes, bytearray)):
        b = bytes(value)
        if len(b) != length:
            raise ValueError(f"address bytes must be {length} bytes, got {len(b)}")
        return b
    if isinstance(value, str) and value.startswith(("0x", "0X")):
        b = _hex_to_bytes(value)
        if len(b) != length:
            raise ValueError(f"address hex must decode to {length} bytes, got {len(b)}")
        return b
    raise ValueError("address must be bytes of correct length or 0x-hex string")


def bytes_to_hex(b: Union[bytes, bytearray, memoryview]) -> str:
    """Encode bytes as '0x' + lowercase hex."""
    return "0x" + bytes(b).hex()


def hex_to_bytes(s: str) -> bytes:
    """Decode '0x' + hex -> bytes. Raises ValueError on malformed input."""
    return _hex_to_bytes(s)


# ----------------------------- Internals -----------------------------


def _normalize_value(v: Any) -> Any:
    # bytes passthrough
    if isinstance(v, (bytes, bytearray, memoryview)):
        return bytes(v)

    # hex string to bytes
    if isinstance(v, str):
        if v.startswith(("0x", "0X")):
            return _hex_to_bytes(v)
        # allow "base64:..." prefix as a convenience (rare)
        if v.startswith("base64:"):
            import base64

            return base64.b64decode(v[7:])
        return v  # plain string (keep as-is)

    # explicit bytes object wrapper
    if isinstance(v, Mapping) and "__bytes" in v:
        raw = v["__bytes"]
        if isinstance(raw, str):
            return _hex_to_bytes(raw) if raw.startswith(("0x", "0X")) else _try_b64(raw)
        if isinstance(raw, (bytes, bytearray, memoryview)):
            return bytes(raw)
        raise ValueError("__bytes must be hex string or bytes")

    # lists/tuples
    if isinstance(v, (list, tuple)):
        return [_normalize_value(x) for x in v]

    # dict (recursive)
    if isinstance(v, Mapping):
        return {k: _normalize_value(val) for k, val in v.items()}

    # numbers / bool / None: pass through
    return v


def _denormalize_value(v: Any) -> Any:
    if isinstance(v, (bytes, bytearray, memoryview)):
        return bytes_to_hex(v)
    if isinstance(v, list):
        return [_denormalize_value(x) for x in v]
    if isinstance(v, tuple):
        return [_denormalize_value(x) for x in v]
    if isinstance(v, Mapping):
        return {k: _denormalize_value(val) for k, val in v.items()}
    return v


def _hex_to_bytes(s: str) -> bytes:
    if not isinstance(s, str):
        raise ValueError("expected string for hex input")
    if not s.startswith(("0x", "0X")):
        raise ValueError("hex string must start with 0x")
    h = s[2:]
    if len(h) % 2 != 0:
        # tolerate odd-length by prefixing a zero nibble
        h = "0" + h
    try:
        return bytes.fromhex(h)
    except ValueError as e:
        raise ValueError(f"invalid hex: {e}") from e


def _try_b64(s: str) -> bytes:
    import base64

    try:
        return base64.b64decode(s, validate=True)
    except Exception as e:
        raise ValueError("invalid base64 in __bytes") from e


__all__ = [
    "normalize_args",
    "normalize_value",
    "denormalize_result",
    "parse_address",
    "bytes_to_hex",
    "hex_to_bytes",
]
