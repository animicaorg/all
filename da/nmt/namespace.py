"""
Animica • DA • NMT — Namespace id type & range checks.

A *namespace* is a non-negative integer used to partition blob shares in the
Namespaced Merkle Tree (NMT). This module provides:

  • `NamespaceId`: a tiny validated int subclass
  • `NamespaceRange`: validated (min,max) pair
  • Helpers to validate, classify (reserved vs user), and compute ranges

The hard limits (bit width, reserved range) are sourced from `da.constants` if
present; sensible defaults are used otherwise.

Defaults (when `da.constants` is unavailable)
---------------------------------------------
  • NAMESPACE_BITS = 32
  • RESERVED range: [0, 15]
  • USER range:     [16, 2^32-1]

These defaults are conservative and match typical "small reserved header space"
designs. Networks may override via `da/constants.py`.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Optional

# -------------------------- Limits from configuration -------------------------

# Try importing canonical limits from da.constants; fall back to defaults.
try:  # pragma: no cover - exercised in integration
    from ..constants import (  # type: ignore
        NAMESPACE_BITS as _NAMESPACE_BITS,
        NAMESPACE_RESERVED_MIN as _RES_MIN,
        NAMESPACE_RESERVED_MAX as _RES_MAX,
        NAMESPACE_USER_MIN as _USER_MIN,
    )
    _HAVE_LIMITS = True
except Exception:  # pragma: no cover
    _HAVE_LIMITS = False
    _NAMESPACE_BITS = 32
    _RES_MIN = 0
    _RES_MAX = 15
    _USER_MIN = _RES_MAX + 1

_NAMESPACE_MAX = (1 << _NAMESPACE_BITS) - 1


# ------------------------------- Core types -----------------------------------

class NamespaceError(ValueError):
    """Raised when a namespace id or range is invalid."""


class NamespaceId(int):
    """
    Small validated int subclass to represent an NMT namespace id.

    Construction:
        ns = NamespaceId(24)
        ns = NamespaceId("0x0010")    # hex (0x…)
        ns = NamespaceId("42")        # decimal

    Properties:
        .is_reserved  -> bool
        .is_user      -> bool
    """
    __slots__ = ()

    def __new__(cls, value: int | str) -> "NamespaceId":
        v = _coerce_to_int(value)
        _validate_ns(v)
        return int.__new__(cls, v)

    @property
    def is_reserved(self) -> bool:
        return _RES_MIN <= int(self) <= _RES_MAX

    @property
    def is_user(self) -> bool:
        return int(self) >= _USER_MIN


@dataclass(frozen=True)
class NamespaceRange:
    """
    A validated [min, max] namespace range (inclusive).

    Invariants:
      • 0 <= min <= max <= NAMESPACE_MAX
    """
    min: NamespaceId
    max: NamespaceId

    def __post_init__(self) -> None:  # type: ignore[override]
        mn = int(self.min)
        mx = int(self.max)
        if mn < 0 or mx < 0:
            raise NamespaceError("namespace ids must be non-negative")
        if mn > mx:
            raise NamespaceError("namespace range must satisfy min <= max")
        if mx > _NAMESPACE_MAX:
            raise NamespaceError(
                f"namespace id exceeds {_NAMESPACE_MAX} (NAMESPACE_BITS={_NAMESPACE_BITS})"
            )

    @property
    def width(self) -> int:
        """Number of distinct namespace ids covered by the range."""
        return int(self.max) - int(self.min) + 1

    def contains(self, ns: int | NamespaceId) -> bool:
        nsv = int(ns)
        return int(self.min) <= nsv <= int(self.max)


# ------------------------------- Public API -----------------------------------

def validate(ns: int | str) -> None:
    """Raise NamespaceError if `ns` is invalid."""
    _validate_ns(_coerce_to_int(ns))


def is_reserved(ns: int | str) -> bool:
    """Return True if `ns` lies in the reserved range [RES_MIN, RES_MAX]."""
    n = _coerce_to_int(ns)
    _validate_ns(n)
    return _RES_MIN <= n <= _RES_MAX


def is_user(ns: int | str) -> bool:
    """Return True if `ns` lies in the user-allocatable range [USER_MIN, MAX]."""
    n = _coerce_to_int(ns)
    _validate_ns(n)
    return n >= _USER_MIN


def clamp_to_user(ns: int | str) -> NamespaceId:
    """
    Clamp `ns` into the user range by lifting it to USER_MIN if it falls
    in the reserved band. Raises if out of absolute bounds.
    """
    n = _coerce_to_int(ns)
    _validate_ns(n)
    return NamespaceId(max(n, _USER_MIN))


def compute_range(namespaces: Iterable[int | str]) -> NamespaceRange:
    """
    Compute the minimal NamespaceRange that covers the given ids.

    Raises NamespaceError if the iterable is empty or any id is invalid.
    """
    it = list(namespaces)
    if not it:
        raise NamespaceError("cannot compute range over empty iterable")
    vals = [NamespaceId(x) for x in it]
    return NamespaceRange(min(vals), max(vals))


def next_user_namespace(prev: Optional[int | str]) -> NamespaceId:
    """
    Return a reasonable "next" user namespace id:
      • If prev is None → USER_MIN
      • Else → min(prev+1, NAMESPACE_MAX), clamped to USER_MIN+ when needed
    """
    if prev is None:
        return NamespaceId(_USER_MIN)
    p = _coerce_to_int(prev)
    _validate_ns(p)
    n = p + 1
    if n < _USER_MIN:
        n = _USER_MIN
    if n > _NAMESPACE_MAX:
        raise NamespaceError("exhausted namespace id space")
    return NamespaceId(n)


# ------------------------------ Internal utils --------------------------------

def _coerce_to_int(v: int | str) -> int:
    if isinstance(v, int):
        return v
    s = v.strip().lower()
    if s.startswith("0x"):
        try:
            return int(s, 16)
        except ValueError as e:
            raise NamespaceError(f"invalid hex namespace id: {v!r}") from e
    try:
        return int(s, 10)
    except ValueError as e:
        raise NamespaceError(f"invalid decimal namespace id: {v!r}") from e


def _validate_ns(n: int) -> None:
    if n < 0:
        raise NamespaceError("namespace id must be non-negative")
    if n > _NAMESPACE_MAX:
        raise NamespaceError(
            f"namespace id {n} exceeds maximum {_NAMESPACE_MAX} "
            f"(NAMESPACE_BITS={_NAMESPACE_BITS})"
        )


__all__ = [
    "NamespaceError",
    "NamespaceId",
    "NamespaceRange",
    "validate",
    "is_reserved",
    "is_user",
    "clamp_to_user",
    "compute_range",
    "next_user_namespace",
]
