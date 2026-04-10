"""
Animica Randomness Beacon package.

This package provides the commit→reveal→VDF→mix beacon used by:
- consensus tie-breakers,
- AICF provider assignment shuffles,
- VM `random(bytes)` syscall mixing,
- sampling seeds for auditors.

Only light, stable exports are surfaced here to avoid import cycles.
"""

from __future__ import annotations

# Public version string (lazy fallback during early bootstrap)
try:
    from .version import __version__  # type: ignore
except Exception:  # pragma: no cover
    __version__ = "0.0.0+dev"

__all__ = ["__version__"]
