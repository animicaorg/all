from __future__ import annotations

"""
Common API model types: Hex/Hash/Address, ChainId, and Pagination.

- Hex:      0x-prefixed, even-length, lowercase hex string.
- Hash:     0x + 64 hex chars (32 bytes), lowercase.
- Address:  bech32m "anim1…" address with valid charset and length bounds.
- ChainId:  positive integer (> 0).

These are defined as Pydantic-friendly type aliases (for v2) plus helpers.
We keep validation light in this layer; deeper checks (e.g., full bech32m
checksum or chain-specific rules) are performed downstream in adapters.
"""

import re
from typing import Optional, Annotated

try:
    # Pydantic v2
    from pydantic import BaseModel, Field, AfterValidator, PositiveInt
    _IS_PYDANTIC_V2 = True
except Exception:  # pragma: no cover - fallback for v1 users
    from pydantic.v1 import BaseModel, Field, PositiveInt, validator  # type: ignore
    AfterValidator = None  # type: ignore
    _IS_PYDANTIC_V2 = False


# ----------------------------- HEX HELPERS -----------------------------

_HEX_RE = re.compile(r"^0x[0-9a-f]+$")


def _normalize_hex(v: str) -> str:
    if not isinstance(v, str):
        raise TypeError("value must be a string")
    v = v.strip()
    if not v.startswith("0x"):
        raise ValueError("hex value must start with 0x")
    h = v[2:].lower()
    if len(h) == 0 or len(h) % 2 != 0:
        raise ValueError("hex nibble length must be even and non-zero")
    if not re.fullmatch(r"[0-9a-f]+", h):
        raise ValueError("hex contains non-hex characters")
    return "0x" + h


def _validate_hex(v: str) -> str:
    return _normalize_hex(v)


def _validate_hash(v: str) -> str:
    v = _normalize_hex(v)
    if len(v) != 66:  # 0x + 64 hex
        raise ValueError("hash must be 0x + 64 hex chars")
    return v


# --------------------------- ADDRESS HELPERS ---------------------------

# bech32m character set (lowercase only)
_BECH32_CHARS = "qpzry9x8gf2tvdw0s3jn54khce6mua7l"
# Minimal structural validation for anim1… addresses.
_ADDR_RE = re.compile(rf"^(?P<hrp>anim)1(?P<data>[{_BECH32_CHARS}]+)$")


def _validate_address(v: str) -> str:
    """
    Validate an Animica bech32m address syntactically.

    Notes:
    - Ensures lowercase, hrp 'anim', allowed charset, and practical length bounds.
    - Full bech32m checksum verification is performed in adapters (pq_addr).
    """
    if not isinstance(v, str):
        raise TypeError("address must be a string")
    s = v.strip()
    if s != s.lower():
        raise ValueError("address must be lowercase")
    m = _ADDR_RE.match(s)
    if not m:
        raise ValueError("address must be bech32m with hrp 'anim' (anim1...)")
    data = m.group("data")
    # Bech32 spec: total length <= 90; require a reasonable payload length.
    if not (8 <= len(data) <= 82):
        raise ValueError("address length out of bounds")
    return s


# --------------------------- PUBLIC TYPE ALIASES -----------------------

if _IS_PYDANTIC_V2:
    Hex = Annotated[str, AfterValidator(_validate_hex)]
    Hash = Annotated[str, AfterValidator(_validate_hash)]
    Address = Annotated[str, AfterValidator(_validate_address)]
    # PositiveInt already validates > 0
    ChainId = PositiveInt
else:  # pragma: no cover - v1 compatibility path
    # In v1 we don't have AfterValidator; we validate in models that use them.
    Hex = str  # type: ignore
    Hash = str  # type: ignore
    Address = str  # type: ignore
    ChainId = PositiveInt  # type: ignore


__all__ = ["Hex", "Hash", "Address", "ChainId", "Pagination"]


# ------------------------------- MODELS --------------------------------


class Pagination(BaseModel):
    """
    Cursor-first pagination with an optional page fallback.

    Fields
    ------
    cursor: Optional[str]
        Opaque cursor string returned by list endpoints. If provided, takes precedence over `page`.
    limit: int
        Max items to return. Bounds: 1..1000. Default: 50.
    page: Optional[int]
        1-based page index (fallback when cursor is absent). Ignored when `cursor` is set.
    """

    cursor: Optional[str] = Field(default=None, description="Opaque cursor; if set, ignores `page`.")
    limit: int = Field(default=50, ge=1, le=1000, description="Max items to return (1..1000).")
    page: Optional[int] = Field(default=None, ge=1, description="1-based page index; ignored if cursor is set.")

    if not _IS_PYDANTIC_V2:  # pragma: no cover - v1 validation hooks
        # pydantic v1 validators for Hex/Hash/Address are not defined here because
        # this model doesn't carry those types; included as a pattern reference.
        pass

    class Config:  # type: ignore[override]
        extra = "forbid"
        anystr_strip_whitespace = True


# ----------------------------- UTILITIES -------------------------------


def strip_0x(v: str) -> str:
    """Return hex string without 0x prefix (no validation)."""
    return v[2:] if isinstance(v, str) and v.startswith("0x") else v


def ensure_0x(v: str) -> str:
    """Ensure a 0x prefix on a (possibly already prefixed) hex string."""
    return v if isinstance(v, str) and v.startswith("0x") else f"0x{v}"


# Back-compat helpers for external callers (do not export by default)
def is_hex(value: str) -> bool:  # pragma: no cover - tiny helper
    try:
        _validate_hex(value)
        return True
    except Exception:
        return False


def is_hash(value: str) -> bool:  # pragma: no cover
    try:
        _validate_hash(value)
        return True
    except Exception:
        return False


def is_address(value: str) -> bool:  # pragma: no cover
    try:
        _validate_address(value)
        return True
    except Exception:
        return False
