"""
Animica • DA Utilities package

This subpackage groups small, reusable helpers used across the Data
Availability (DA) module:

  - da.utils.bytes   : byte/hex/varint helpers, chunking utilities
  - da.utils.hash    : SHA3-256/512 wrappers and domain-tagged hashing
  - da.utils.merkle  : generic Merkle helpers (used by NMT/proofs code)

It uses lazy submodule loading so importing `da.utils` is cheap:

    from da import utils
    h = utils.hash.sha3_256(b"...")
    leaf = utils.merkle.Node(...)

Version is inherited from the parent `da` package.
"""
from __future__ import annotations

from importlib import import_module
from typing import Any

# Re-export version from the top-level DA package.
try:
    from ..version import __version__  # type: ignore[F401]
except Exception:  # pragma: no cover - only hit during partial tree setups
    __version__ = "0.0.0+unknown"

# Submodules exposed via lazy loading.
_SUBMODULES = ("bytes", "hash", "merkle")

def __getattr__(name: str) -> Any:
    """
    Lazily import and return one of the known utility submodules.

    Example:
        >>> from da import utils
        >>> utils.hash  # triggers import of da.utils.hash
    """
    if name in _SUBMODULES:
        try:
            return import_module(f"{__name__}.{name}")
        except ModuleNotFoundError as e:  # pragma: no cover
            raise AttributeError(
                f"Optional submodule 'da.utils.{name}' is not available yet."
            ) from e
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

def __dir__() -> list[str]:
    """Advertise lazy members in dir()."""
    return sorted(list(globals().keys()) + list(_SUBMODULES))

__all__ = list(_SUBMODULES)
