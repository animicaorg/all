"""
execution.state — state subsystem (accounts, storage, journal, snapshots, events).

This package provides the deterministic state layer used by the execution
engine. To keep import-time overhead low and avoid circulars, the common
symbols are lazily re-exported from their submodules on first access.

Submodules (planned):
- accounts:       Account records (nonce, balance, code hash)
- storage:        Per-account storage view (key/value)
- view:           Read-only typed getters over the current state
- journal:        Journaling writes, checkpoints, revert/commit
- snapshots:      Snapshot manager & diff application
- events:         Event/log sink backends
- receipts:       Build receipts & logs bloom/root
- apply_balance:  Safe balance transfer; fee debit/credit helpers
- access_tracker: Track touched addresses/keys for access lists
"""

from __future__ import annotations

from importlib import import_module as _imp
from typing import Any, Dict, Tuple

# Public version string
try:
    from .version import __version__  # type: ignore
except Exception:  # pragma: no cover - during bootstrap
    __version__ = "0.0.0-dev"

# Map of public attributes → (submodule, symbol)
_exports: Dict[str, Tuple[str, str]] = {
    # Core state objects
    "Account": ("accounts", "Account"),
    "StorageView": ("storage", "StorageView"),
    "StateView": ("view", "StateView"),
    "Journal": ("journal", "Journal"),
    "SnapshotManager": ("snapshots", "SnapshotManager"),
    # Events & receipts
    "EventSink": ("events", "EventSink"),
    "build_receipts": ("receipts", "build_receipts"),
    # Balance helpers
    "safe_transfer": ("apply_balance", "safe_transfer"),
    "debit_fee": ("apply_balance", "debit_fee"),
    "credit_fee": ("apply_balance", "credit_fee"),
    # Access tracking
    "AccessTracker": ("access_tracker", "AccessTracker"),
}

__all__ = tuple(["__version__", *_exports.keys()])


def __getattr__(name: str) -> Any:
    """
    Lazy attribute loader to avoid import-time dependency tangles.
    """
    if name in _exports:
        submod, symbol = _exports[name]
        mod = _imp(f"{__name__}.{submod}")
        return getattr(mod, symbol)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def __dir__() -> list[str]:  # pragma: no cover
    return sorted(list(globals().keys()) + list(__all__))
