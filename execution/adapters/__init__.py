"""
execution.adapters — integration bridges for the execution engine.

This package contains thin adapter layers that connect the execution module to
other subsystems:

  • state_db     — bridge to core.db.state_db (batch reads/writes, snapshots)
  • block_db     — attach receipts/logs to blocks via core.db.block_db
  • params       — load/validate ChainParams from core.types.params
  • da_caps      — blob size/cost guards (hooks for da/ integration)
  • vm_entry     — call into vm_py runtime (feature-flagged)

Design notes
------------
Adapters are intentionally small and side-effect free. They translate types,
perform shallow validation, and centralize cross-module imports so the core
execution code remains decoupled and easily testable.

All submodules are imported lazily (PEP 562) so importing `execution.adapters`
is cheap and safe in environments where optional dependencies (e.g. RocksDB,
vm_py) are unavailable.
"""

from __future__ import annotations

from importlib import import_module
from typing import Any, List

__all__ = [
    "state_db",
    "block_db",
    "params",
    "da_caps",
    "vm_entry",
]

def __getattr__(name: str) -> Any:  # PEP 562 lazy import
    if name in __all__:
        return import_module(f"{__name__}.{name}")
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

def __dir__() -> List[str]:
    return sorted(list(globals().keys()) + __all__)
