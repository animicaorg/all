"""
execution.scheduler — scheduling strategies for applying transactions/blocks.

This package hosts multiple schedulers:

- serial:     Deterministic single-threaded executor (canonical).
- optimistic: Optimistic-parallel prototype with conflict detection & merge.
- lockset:    Capture read/write locksets from access tracking.
- merge:      Merge results from speculative runs; revert conflicted.
- deps:       Build a minimal dependency graph from access lists.

Design
------
We expose submodules lazily (PEP 562) so importing `execution.scheduler`
doesn't import heavy dependencies until actually used. A small convenience
factory `get_default_scheduler` is provided for common flows.

Example
-------
    from execution.scheduler import get_default_scheduler
    sched = get_default_scheduler(mode="serial")
    result = sched.apply_block(block, state_view, gas_table)

"""

from __future__ import annotations

import importlib
from typing import Any

# -------------------------------- Version ----------------------------------

try:  # Prefer the execution module's version if available
    from ..version import __version__  # type: ignore
except Exception:  # pragma: no cover
    __version__ = "0.0.0"

# ----------------------------- Lazy submodules ------------------------------

_SUBMODULES = {
    "serial",
    "optimistic",
    "lockset",
    "merge",
    "deps",
}

__all__ = [
    "__version__",
    # submodules
    "serial",
    "optimistic",
    "lockset",
    "merge",
    "deps",
    # helpers
    "get_default_scheduler",
]


def __getattr__(name: str) -> Any:  # PEP 562 lazy import
    if name in _SUBMODULES:
        return importlib.import_module(f"{__name__}.{name}")
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def __dir__() -> list[str]:  # pragma: no cover - trivial
    return sorted(list(globals().keys()) + list(_SUBMODULES))


# ------------------------------ Convenience --------------------------------


def get_default_scheduler(mode: str = "serial", **kwargs: Any) -> Any:
    """
    Construct a scheduler by mode name.

    Parameters
    ----------
    mode : {"serial","optimistic"}
        The scheduler strategy to use. Defaults to "serial".
    **kwargs : Any
        Extra keyword args forwarded to the scheduler constructor.

    Returns
    -------
    Any
        An instance of the requested scheduler. Concrete type depends on mode.

    Raises
    ------
    ValueError
        If an unknown mode is requested.
    """
    m = mode.strip().lower()
    if m in ("serial", "deterministic", "single", "singlethread"):
        # Import only when needed
        SerialScheduler = importlib.import_module(f"{__name__}.serial").SerialScheduler  # type: ignore[attr-defined]
        return SerialScheduler(**kwargs)
    if m in ("optimistic", "parallel", "opt"):
        OptimisticScheduler = importlib.import_module(f"{__name__}.optimistic").OptimisticScheduler  # type: ignore[attr-defined]
        return OptimisticScheduler(**kwargs)
    raise ValueError(
        f"unknown scheduler mode {mode!r}; expected 'serial' or 'optimistic'"
    )
