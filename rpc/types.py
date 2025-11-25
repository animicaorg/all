"""
Animica RPC type helpers.

- Lightweight "newtypes" for Hex strings, Hash (32-byte) hex strings, and bech32m Addresses.
- Strict hex parsing/formatting helpers (0x-prefixed, even-length).
- Bech32m encode/decode for addresses using the payload layout:
      payload = alg_id(1 byte) || sha3_256(pubkey)(32 bytes)
  The human-readable-part (hrp) is network-specific (e.g., "anim").
- Zero business logic: this module does *not* reach into the DB; it only validates/normalizes.

Dependencies:
- pq.py.utils.bech32 (bech32m codec + convertbits)
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, NewType, Optional, Tuple, Union, overload

try:
    from pq.py.utils.bech32 import (
        bech32_encode,
        bech32_decode,
        convertbits,
        Encoding,
    )
except ImportError:  # Older pq builds expose functions without Encoding enum
    from pq.py.utils import bech32 as _bech32  # type: ignore

    bech32_encode = _bech32.bech32_encode  # type: ignore[attr-defined]
    bech32_decode = _bech32.bech32_decode  # type: ignore[attr-defined]
    convertbits = _bech32.convertbits  # type: ignore[attr-defined]

    class Encoding:  # type: ignore[override]
        BECH32M = "bech32m"

# ───────────────────────────────────────────────────────────────────────────────
# Newtypes (for readability in annotations)
# ───────────────────────────────────────────────────────────────────────────────

HexStr = NewType("HexStr", str)          # e.g., "0xdeadbeef"
HashHex32 = NewType("HashHex32", str)    # e.g., "0x" + 64 hex chars (32 bytes)
Address = NewType("Address", str)        # bech32m-encoded (e.g., "anim1...")

HEX_PREFIX = "0x"


# ───────────────────────────────────────────────────────────────────────────────
# Hex helpers
# ───────────────────────────────────────────────────────────────────────────────

def is_hex_str(s: str, *, prefixed: bool = True, even: bool = True) -> bool:
    """
    Return True iff `s` is a valid hex string.
    - If prefixed=True, `s` must start with "0x".
    - If even=True, the number of hex nybbles after the prefix must be even.
    """
    if not isinstance(s, str):
        return False
    if prefixed:
        if not s.startswith(HEX_PREFIX):
            return False
        h = s[2:]
    else:
        h = s
    if h == "":
        return True if not even else False
    try:
        int(h, 16)
    except ValueError:
        return False
    return (len(h) % 2 == 0) if even else True


def to_hex(data: Union[bytes, bytearray, memoryview], *, prefix: bool = True) -> HexStr:
    """
    Bytes → hex string ("0x..." if prefix=True).
    """
    if not isinstance(data, (bytes, bytearray, memoryview)):
        raise TypeError("to_hex expects bytes-like")
    h = bytes(data).hex()
    return HexStr(HEX_PREFIX + h if prefix else h)


def from_hex(s: str, *, require_prefix: bool = True) -> bytes:
    """
    Hex string → bytes. Requires "0x" by default; enforces even nybbles.
    """
    if not isinstance(s, str):
        raise TypeError("from_hex expects str")
    if require_prefix and not s.startswith(HEX_PREFIX):
        raise ValueError("hex string must start with 0x")
    h = s[2:] if (require_prefix and s.startswith(HEX_PREFIX)) else s
    if len(h) % 2 != 0:
        raise ValueError("hex string must have even nybbles")
    try:
        return bytes.fromhex(h)
    except ValueError as e:
        raise ValueError(f"invalid hex: {e}") from e


def ensure_hex(s: str) -> HexStr:
    """
    Ensure `s` is a valid 0x-prefixed, even-length hex string; return as HexStr.
    """
    if not is_hex_str(s, prefixed=True, even=True):
        raise ValueError("invalid hex string (must be 0x-prefixed, even length, hex digits only)")
    return HexStr(s)


def ensure_hash32_hex(s: str) -> HashHex32:
    """
    Ensure `s` is a valid 32-byte hex string (0x + 64 nybbles).
    """
    s_hex = ensure_hex(s)
    if len(s_hex) != 2 + 64:
        raise ValueError("hash must be 32 bytes (0x + 64 hex chars)")
    return HashHex32(s_hex)


def parse_hash32(s: str) -> bytes:
    """
    Parse 32-byte hash hex → bytes; raises ValueError on mismatch.
    """
    hh = ensure_hash32_hex(s)
    out = from_hex(hh)
    if len(out) != 32:
        # Redundant guard (ensure_hash32_hex already checks)
        raise ValueError("hash must be 32 bytes")
    return out


# ───────────────────────────────────────────────────────────────────────────────
# Address helpers (bech32m)
# payload = alg_id(1 byte) || sha3_256(pubkey)(32 bytes)
# ───────────────────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class AddressParts:
    hrp: str
    alg_id: int
    pubkey_hash: bytes  # 32 bytes

    def __post_init__(self) -> None:
        if not (0 <= self.alg_id <= 255):
            raise ValueError("alg_id must fit into one byte")
        if not isinstance(self.pubkey_hash, (bytes, bytearray, memoryview)):
            raise TypeError("pubkey_hash must be bytes-like")
        if len(self.pubkey_hash) != 32:
            raise ValueError("pubkey_hash must be 32 bytes")
        if not self.hrp or not isinstance(self.hrp, str):
            raise ValueError("hrp must be non-empty str")


def _payload_from_parts(parts: AddressParts) -> bytes:
    return bytes([parts.alg_id]) + bytes(parts.pubkey_hash)


def _is_bech32m(enc: object) -> bool:
    target = getattr(Encoding, "BECH32M", "bech32m")
    if isinstance(enc, str):
        return enc.lower() == str(target).lower()
    return enc == target


def _parts_from_payload(hrp: str, payload: bytes) -> AddressParts:
    if len(payload) != 33:
        raise ValueError("address payload must be 33 bytes (1 alg_id + 32 pubkey_hash)")
    return AddressParts(hrp=hrp, alg_id=payload[0], pubkey_hash=payload[1:33])


def encode_address(parts: AddressParts) -> Address:
    """
    Build bech32m address from parts.
    """
    payload = _payload_from_parts(parts)
    data5 = convertbits(payload, frombits=8, tobits=5, pad=True)
    if data5 is None:
        raise ValueError("convertbits failed (8→5)")
    addr = bech32_encode(parts.hrp, data5, Encoding.BECH32M)
    if not isinstance(addr, str) or addr == "":
        raise ValueError("bech32_encode failed")
    return Address(addr)


def decode_address(addr: str, *, allowed_hrps: Optional[Iterable[str]] = None) -> AddressParts:
    """
    Parse bech32m address → (hrp, alg_id, pubkey_hash).
    - Validates checksum, bech32m encoding, and payload size.
    - If allowed_hrps is provided, hrp must be in that set.
    """
    if not isinstance(addr, str):
        raise TypeError("address must be str")
    hrp, data, enc = bech32_decode(addr)
    if hrp is None or data is None or enc is None:
        raise ValueError("invalid bech32 address (decode failed)")
    if not _is_bech32m(enc):
        raise ValueError("address must use bech32m encoding")
    if allowed_hrps is not None:
        allowed = set(allowed_hrps)
        if hrp not in allowed:
            raise ValueError(f"hrp '{hrp}' not allowed (expected one of {sorted(allowed)})")
    payload = convertbits(data, frombits=5, tobits=8, pad=False)
    if payload is None:
        raise ValueError("convertbits failed (5→8)")
    return _parts_from_payload(hrp, bytes(payload))


def is_address(addr: str, *, allowed_hrps: Optional[Iterable[str]] = None) -> bool:
    try:
        decode_address(addr, allowed_hrps=allowed_hrps)
        return True
    except Exception:
        return False


# ───────────────────────────────────────────────────────────────────────────────
# Convenience: round-trips and formatting
# ───────────────────────────────────────────────────────────────────────────────

def address_roundtrip_ok(addr: str) -> bool:
    """
    Decode → re-encode → compare case-sensitively.
    """
    try:
        parts = decode_address(addr)
        return encode_address(parts) == addr
    except Exception:
        return False


def format_address(parts: AddressParts) -> Address:
    """
    Alias of encode_address for symmetry; returns Address newtype.
    """
    return encode_address(parts)


# ───────────────────────────────────────────────────────────────────────────────
# Public exports
# ───────────────────────────────────────────────────────────────────────────────

__all__ = [
    # newtypes
    "HexStr",
    "HashHex32",
    "Address",
    # hex helpers
    "is_hex_str",
    "to_hex",
    "from_hex",
    "ensure_hex",
    "ensure_hash32_hex",
    "parse_hash32",
    # address helpers
    "AddressParts",
    "encode_address",
    "decode_address",
    "is_address",
    "address_roundtrip_ok",
    "format_address",
    # constants
    "HEX_PREFIX",
]
