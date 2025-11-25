"""
execution.access_list — helpers for transaction access lists.

This package provides:
- build: Construct an access list from an execution trace (read/write keys).
- merge:  Union/intersection utilities for batching and scheduler planning.

Typical usage:
    from execution.access_list import build_access_list, merge_union, merge_intersection
"""

from __future__ import annotations

# Re-exports (optional at import time; tests may import this package before
# the submodules are generated). We guard imports so the package is importable
# even if only a subset of files is present during incremental bring-up.

__all__: list[str] = []

try:
    from .build import build_access_list  # type: ignore
    __all__.append("build_access_list")
except Exception:
    pass

try:
    from .merge import merge_union, merge_intersection  # type: ignore
    __all__ += ["merge_union", "merge_intersection"]
except Exception:
    pass
