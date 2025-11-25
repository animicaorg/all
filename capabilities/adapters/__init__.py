"""
capabilities.adapters
=====================

Thin, optional adapters used by the capabilities subsystem to talk to
other subsystems (DA, AICF, zk, randomness, execution/core state, proofs).

This package intentionally *does not* hard-depend on those subsystems so
that a minimal build can still import `capabilities.*`. Use `load()` to
dynamically import an adapter module only when you actually need it.

Example
-------
>>> from capabilities.adapters import load, available
>>> mod = load("da")          # loads capabilities.adapters.da
>>> "da" in available()       # True if import succeeded
"""

from __future__ import annotations

from importlib import import_module
from typing import Dict, List, Optional
from types import ModuleType

# Canonical short-name → module path mapping
_ADAPTERS: Dict[str, str] = {
    "da": "capabilities.adapters.da",
    "aicf": "capabilities.adapters.aicf",
    "zk": "capabilities.adapters.zk",
    "randomness": "capabilities.adapters.randomness",
    "execution_state": "capabilities.adapters.execution_state",
    "proofs": "capabilities.adapters.proofs",
}

def load(name_or_module: str) -> ModuleType:
    """
    Dynamically import an adapter by short name (e.g. 'da', 'aicf') or full module path.
    Raises ImportError with a helpful message if the adapter is unavailable.
    """
    module_path = _ADAPTERS.get(name_or_module, name_or_module)
    try:
        return import_module(module_path)
    except Exception as e:  # pragma: no cover - environment/import specific
        raise ImportError(f"Failed to load adapter '{name_or_module}' ({module_path}): {e}") from e

def available() -> List[str]:
    """
    Return the list of short adapter names that successfully import in this environment.
    """
    ok: List[str] = []
    for short, modpath in _ADAPTERS.items():
        try:
            import_module(modpath)
            ok.append(short)
        except Exception:
            # Silently ignore; caller can inspect the returned list
            pass
    return ok

def has(name_or_module: str) -> bool:
    """
    Fast check whether an adapter can be imported.
    """
    try:
        load(name_or_module)
        return True
    except ImportError:
        return False

__all__ = ["load", "available", "has", "_ADAPTERS"]
