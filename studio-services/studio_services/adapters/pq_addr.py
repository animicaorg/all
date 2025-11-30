"""
pq_addr.py
----------

Address validation and parsing using Animica's PQ bech32m rules.

This adapter prefers the canonical pq bech32 implementation if present,
then falls back to the SDK helpers if available, and finally uses an
internal, standards-compliant bech32m decoder (BIP-350).

Addresses are bech32m-encoded with HRP "anim" (mainnet) and variants
(e.g., "animt" testnet, "animd" devnet). The payload is:

    payload = alg_id (1 byte) || sha3_256(pubkey) (32 bytes)

so 33 bytes total prior to bech32m 5-bit conversion and checksum.

Public helpers:
- `is_valid_address(addr: str, *, hrp: str | None = None) -> bool`
- `ensure_valid_address(addr: str, *, hrp: str | None = None) -> str`
     (returns normalized lowercase bech32m string or raises ValueError)
- `parse_address(addr: str) -> ParsedAddress`

If you already depend on `omni_sdk.address`, that remains the single
source of truth. This module is a thin, dependency-tolerant facade for
studio-services.
"""

from __future__ import annotations

import hashlib
import logging
import re
from dataclasses import dataclass
from typing import Iterable, List, Optional, Tuple

logger = logging.getLogger(__name__)

# ------------------------- Optional upstream integrations ---------------------

# Prefer pq's bech32 if available.
_pq_bech32 = None
try:  # pragma: no cover - availability depends on deployment layout
    from pq.py.utils import bech32 as _pq_bech32  # type: ignore
except Exception:
    _pq_bech32 = None

# Prefer SDK's address helpers if available.
_sdk_addr = None
try:  # pragma: no cover - availability depends on deployment layout
    from omni_sdk import address as _sdk_addr  # type: ignore
except Exception:
    _sdk_addr = None


# ------------------------------- Data types ----------------------------------


@dataclass(frozen=True)
class ParsedAddress:
    hrp: str
    alg_id: int
    digest: bytes  # 32-byte sha3-256(pubkey)


# ----------------------------- Internal bech32m -------------------------------
# Minimal, self-contained BIP-350 bech32m decoder (no encoding needed here).

_CHARSET = "qpzry9x8gf2tvdw0s3jn54khce6mua7l"
_CHARSET_DICT = {c: i for i, c in enumerate(_CHARSET)}
_BECH32M_CONST = 0x2BC830A3

# Simple pre-check (length + charset + one '1' separator, case rules)
_BECH_RE = re.compile(r"^[a-z0-9]{1,83}1[ac-hj-np-z02-9]{6,}$")


def _bech32_hrp_expand(s: str) -> List[int]:
    return [ord(x) >> 5 for x in s] + [0] + [ord(x) & 31 for x in s]


def _bech32_polymod(values: Iterable[int]) -> int:
    GEN = (0x3B6A57B2, 0x26508E6D, 0x1EA119FA, 0x3D4233DD, 0x2A1462B3)
    chk = 1
    for v in values:
        top = chk >> 25
        chk = (chk & 0x1FFFFFF) << 5 ^ v
        for i in range(5):
            if (top >> i) & 1:
                chk ^= GEN[i]
    return chk


def _bech32_verify_checksum(hrp: str, data: List[int]) -> bool:
    return _bech32_polymod(_bech32_hrp_expand(hrp) + data) == _BECH32M_CONST


def _bech32_decode(addr: str) -> Tuple[str, List[int]]:
    """
    Decode a bech32m string into (hrp, data) where `data` excludes checksum
    and contains 5-bit integers.
    Raises ValueError on any failure.
    """
    if not isinstance(addr, str):
        raise ValueError("address must be a string")
    # Enforce lowercase per bech32 rules (mixed case invalid)
    if any("A" <= c <= "Z" for c in addr) and any("a" <= c <= "z" for c in addr):
        raise ValueError("mixed-case bech32 is invalid")
    s = addr.lower().strip()
    if not _BECH_RE.match(s):
        raise ValueError("address format invalid")
    pos = s.rfind("1")
    hrp, data_part = s[:pos], s[pos + 1 :]
    if not hrp or len(data_part) < 6:
        raise ValueError("invalid hrp or data length")
    try:
        data = [_CHARSET_DICT[c] for c in data_part]
    except KeyError:
        raise ValueError("invalid bech32 characters")
    if not _bech32_verify_checksum(hrp, data):
        raise ValueError("invalid bech32m checksum")
    # Strip 6-char checksum
    return hrp, data[:-6]


def _convertbits(data: Iterable[int], from_bits: int, to_bits: int, pad: bool) -> bytes:
    """General power-of-two base conversion (here: 5 -> 8)."""
    acc = 0
    bits = 0
    ret = bytearray()
    maxv = (1 << to_bits) - 1
    max_acc = (1 << (from_bits + to_bits - 1)) - 1
    for value in data:
        if value < 0 or (value >> from_bits):
            raise ValueError("invalid value for convertbits")
        acc = ((acc << from_bits) | value) & max_acc
        bits += from_bits
        while bits >= to_bits:
            bits -= to_bits
            ret.append((acc >> bits) & maxv)
    if pad:
        if bits:
            ret.append((acc << (to_bits - bits)) & maxv)
    elif bits >= from_bits or ((acc << (to_bits - bits)) & maxv):
        # leftover non-zero
        raise ValueError("non-zero padding in convertbits")
    return bytes(ret)


# ----------------------------- Core operations --------------------------------

_DEFAULT_ALLOWED_HRPS = {"anim", "animt", "animd"}


def _try_sdk_is_valid(addr: str, hrp: Optional[str]) -> Optional[bool]:
    if _sdk_addr is None:
        return None
    # Try a few possible function names defensively
    for fn in ("is_valid_address", "validate", "is_valid"):
        if hasattr(_sdk_addr, fn):
            try:
                return bool(getattr(_sdk_addr, fn)(addr, hrp=hrp))  # type: ignore[misc]
            except TypeError:
                # maybe the function doesn't accept hrp kw
                try:
                    return bool(getattr(_sdk_addr, fn)(addr))
                except Exception:
                    return None
            except Exception:
                return False
    return None


def _try_sdk_normalize(addr: str) -> Optional[str]:
    if _sdk_addr is None:
        return None
    for fn in (
        "normalize_address",
        "normalize",
        "to_checksum",
    ):  # last one unlikely for bech32m
        if hasattr(_sdk_addr, fn):
            try:
                v = getattr(_sdk_addr, fn)(addr)
                if isinstance(v, str):
                    return v
            except Exception:
                return None
    return None


def _try_sdk_parse(addr: str) -> Optional[ParsedAddress]:
    if _sdk_addr is None:
        return None
    for fn in ("parse_address", "decode_address", "decode"):
        if hasattr(_sdk_addr, fn):
            try:
                out = getattr(_sdk_addr, fn)(addr)
                # Accept common shapes: tuple or obj/dict
                if isinstance(out, tuple) and len(out) == 3:
                    hrp, alg_id, digest = out
                    return ParsedAddress(str(hrp), int(alg_id), bytes(digest))
                if isinstance(out, dict):
                    return ParsedAddress(
                        str(out["hrp"]), int(out["alg_id"]), bytes(out["digest"])
                    )
            except Exception:
                return None
    return None


def parse_address(addr: str) -> ParsedAddress:
    """
    Parse an Animica bech32m address and return (hrp, alg_id, digest).
    Raises ValueError for any invalid input.
    """
    # Prefer SDK if present
    parsed = _try_sdk_parse(addr)
    if parsed is not None:
        return parsed

    # Prefer pq's bech32 if present
    if _pq_bech32 is not None:  # pragma: no cover
        try:
            hrp, data = _pq_bech32.bech32_decode(addr)  # type: ignore[attr-defined]
            if hrp is None or data is None:
                raise ValueError("invalid bech32 address")
            if hasattr(_pq_bech32, "bech32_verify_checksum"):
                if not _pq_bech32.bech32_verify_checksum(hrp, data):  # type: ignore[attr-defined]
                    raise ValueError("invalid bech32 checksum")
            # If pq bech32 returns *with* checksum, strip it. Most libs return data without checksum already.
            if len(data) >= 6 and hasattr(_pq_bech32, "bech32_verify_checksum"):
                # heuristic: pq impl may already strip; only strip when checksum verified above on full data
                pass
            raw = _convertbits(data, 5, 8, False)
            if len(raw) != 33:
                raise ValueError(
                    "address payload must be 33 bytes (alg_id + sha3-256 digest)"
                )
            return ParsedAddress(hrp=hrp, alg_id=raw[0], digest=raw[1:])
        except Exception as e:
            raise ValueError(f"invalid address: {e}") from e

    # Internal bech32m path
    hrp, data5 = _bech32_decode(addr)
    raw = _convertbits(data5, 5, 8, False)
    if len(raw) != 33:
        raise ValueError("address payload must be 33 bytes (alg_id + sha3-256 digest)")
    return ParsedAddress(hrp=hrp, alg_id=raw[0], digest=raw[1:])


def is_valid_address(addr: str, *, hrp: Optional[str] = None) -> bool:
    """
    True if `addr` is a syntactically valid bech32m Animica address.
    If `hrp` is provided, it must match exactly.
    """
    # Try SDK fast-path first
    sdk_res = _try_sdk_is_valid(addr, hrp)
    if sdk_res is not None:
        return sdk_res

    try:
        parsed = parse_address(addr)
    except Exception:
        return False

    if hrp is not None:
        return parsed.hrp == hrp
    # Otherwise restrict to common HRPs
    return parsed.hrp in _DEFAULT_ALLOWED_HRPS


def ensure_valid_address(addr: str, *, hrp: Optional[str] = None) -> str:
    """
    Validate and normalize address (lowercase). Raises ValueError on failure.
    """
    if not is_valid_address(addr, hrp=hrp):
        raise ValueError("invalid address")
    # Prefer SDK normalization if available
    norm = _try_sdk_normalize(addr)
    if norm:
        return norm
    return addr.strip().lower()


# ----------------------------- Additional helpers -----------------------------


def sha3_256_hex(b: bytes) -> str:
    """Utility: 0x-prefixed SHA3-256 digest of bytes."""
    return "0x" + hashlib.sha3_256(b).hexdigest()


__all__ = [
    "ParsedAddress",
    "parse_address",
    "is_valid_address",
    "ensure_valid_address",
    "sha3_256_hex",
]
