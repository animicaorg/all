"""
capabilities.host
=================

Host-side syscall providers used by the VM/execution runtime.

This package groups the concrete provider modules:

- ``provider``     — central registry & deterministic dispatch
- ``blob``         — blob_pin(ns, data) → commitment (bridges to da/)
- ``compute``      — ai_enqueue / quantum_enqueue
- ``result_read``  — deterministic result read (next-block consumption)
- ``zk``           — zk_verify(...) helpers
- ``random``       — deterministic randomness stub / beacon mix-in hooks
- ``treasury``     — debit/credit hooks to a treasury account (metered)

To keep imports cheap and avoid circulars during bootstrap, submodules are
**lazily imported** via ``__getattr__`` (PEP 562). You can either import the
modules directly::

    from capabilities.host import provider
    reg = provider.get_registry()

or import symbols from submodules explicitly (preferred to keep type checkers
happy)::

    from capabilities.host.provider import ProviderRegistry
"""

from __future__ import annotations

from importlib import import_module
from types import ModuleType
from typing import Dict

from ..version import __version__  # re-export

# Public submodules exposed at package level (lazy-loaded).
_SUBMODULES: Dict[str, str] = {
    "provider": ".provider",
    "blob": ".blob",
    "compute": ".compute",
    "result_read": ".result_read",
    "zk": ".zk",
    "random": ".random",
    "treasury": ".treasury",
}

__all__ = ["__version__", *list(_SUBMODULES.keys())]


def __getattr__(name: str) -> ModuleType:
    modrel = _SUBMODULES.get(name)
    if modrel is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    return import_module(f"{__name__}{modrel}")


def __dir__() -> list[str]:
    return sorted(list(globals().keys()) + list(_SUBMODULES.keys()))
