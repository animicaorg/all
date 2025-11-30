from __future__ import annotations

"""
aicf.adapters
=============

Thin package marker for adapter modules that bridge AICF to other subsystems
in the node (e.g., execution, randomness beacon, DA, RPC).

Actual adapters live in sibling modules (to be added progressively). This
module exposes a stable import path and the package version for convenience.
"""

from typing import Tuple

try:
    # Reuse the top-level package version if available.
    from ..version import __version__  # type: ignore
except Exception:  # pragma: no cover - fallback in early bootstraps
    __version__ = "0.0.0"

__all__: Tuple[str, ...] = ("__version__",)
