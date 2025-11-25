"""
omni_sdk.types
==============

Lightweight façade for SDK datatypes.

This package exposes two submodules:

- :mod:`omni_sdk.types.core` — core chain types (Tx, Receipt, Block, Head, ...)
- :mod:`omni_sdk.types.abi`  — ABI models & validators

You can import either the modules:

    from omni_sdk.types import core, abi
    tx: core.Tx

or (once the submodules are present) import concrete names directly:

    from omni_sdk.types import Tx, Receipt

This file uses lazy forwarding so importing :mod:`omni_sdk.types` does not
eagerly import heavy dependencies. Attributes are resolved on first access.
"""

from __future__ import annotations

import importlib
from types import ModuleType
from typing import Dict, Iterable, List

__all__ = [
    # Submodules (always available via lazy import)
    "core",
    "abi",
    # Plus selected names forwarded from submodules (populated lazily in __dir__/__getattr__)
]

# Map of friendly name -> absolute module path
_SUBMODULES: Dict[str, str] = {
    "core": "omni_sdk.types.core",
    "abi": "omni_sdk.types.abi",
}

# Optional: names we commonly forward from submodules (best-effort)
_FORWARD_CANDIDATES = (
    # core
    "Tx",
    "Receipt",
    "Block",
    "Head",
    "Log",
    # abi
    "Abi",
    "AbiFunction",
    "AbiEvent",
    "AbiParam",
    "AbiError",
    "validate_abi",
)


def _load_submodule(name: str) -> ModuleType:
    path = _SUBMODULES.get(name)
    if not path:
        raise AttributeError(f"module 'omni_sdk.types' has no attribute '{name}'")
    return importlib.import_module(path)


def __getattr__(name: str):
    # If user asks for a submodule (core/abi), load and return it.
    if name in _SUBMODULES:
        return _load_submodule(name)

    # Otherwise, try to find the attribute in one of our submodules.
    for sm_name in ("core", "abi"):
        try:
            mod = _load_submodule(sm_name)
        except Exception:
            continue
        if hasattr(mod, name):
            return getattr(mod, name)

    raise AttributeError(f"module 'omni_sdk.types' has no attribute '{name}'")


def __dir__() -> List[str]:
    base: List[str] = list(__all__)
    # Add any forwardable names found in available submodules
    seen: set[str] = set(base)
    for sm in ("core", "abi"):
        try:
            mod = _load_submodule(sm)
        except Exception:
            continue
        for n in _FORWARD_CANDIDATES:
            if hasattr(mod, n) and n not in seen:
                base.append(n)
                seen.add(n)
    base.sort()
    return base
