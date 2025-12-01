"""
Animica core.types
==================

This package contains canonical dataclasses and typed aliases for all
chain-level objects:

- params:     ChainParams (subset of spec/params.yaml)
- tx:         Tx (transfer/deploy/call), helpers for hashing/sign-bytes
- receipt:    Receipt (status, gasUsed, logs, topics)
- proof:      HashShare, AIProofRef, QuantumProofRef, StorageHeartbeat, VDFProofRef
- header:     Header (roots, Θ, mixSeed, chainId, etc.)
- block:      Block (header + txs + proofs [+ optional receipts])

To keep import order flexible and avoid circulars during early bring-up,
this module exposes **lazy re-exports**: attributes resolve on first access.

Example
-------
>>> from core.types import Tx, Header, ChainParams
>>> from core.types import tx, header  # submodules available lazily too
"""

from __future__ import annotations

import importlib
from typing import TYPE_CHECKING

__all__ = [
    # submodules
    "params",
    "tx",
    "receipt",
    "proof",
    "header",
    "block",
    # common re-exported symbols
    "ChainParams",
    "Tx",
    "Receipt",
    "HashShare",
    "AIProofRef",
    "QuantumProofRef",
    "StorageHeartbeat",
    "VDFProofRef",
    "Header",
    "Block",
]

# Map attribute → module path (for submodule-style access)
_SUBMODULES = {
    "params": "core.types.params",
    "tx": "core.types.tx",
    "receipt": "core.types.receipt",
    "proof": "core.types.proof",
    "header": "core.types.header",
    "block": "core.types.block",
}

# Map symbol → (module path, symbol name) for class-level re-exports
_SYMBOLS = {
    "ChainParams": ("core.types.params", "ChainParams"),
    "Tx": ("core.types.tx", "Tx"),
    "Receipt": ("core.types.receipt", "Receipt"),
    "HashShare": ("core.types.proof", "HashShare"),
    "AIProofRef": ("core.types.proof", "AIProofRef"),
    "QuantumProofRef": ("core.types.proof", "QuantumProofRef"),
    "StorageHeartbeat": ("core.types.proof", "StorageHeartbeat"),
    "VDFProofRef": ("core.types.proof", "VDFProofRef"),
    "Header": ("core.types.header", "Header"),
    "Block": ("core.types.block", "Block"),
}


def __getattr__(name: str):
    # Lazy submodule import (e.g., core.types.tx)
    if name in _SUBMODULES:
        return importlib.import_module(_SUBMODULES[name])
    # Lazy symbol re-export (e.g., core.types.Tx)
    target = _SYMBOLS.get(name)
    if target:
        mod = importlib.import_module(target[0])
        return getattr(mod, target[1])
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def __dir__():
    base = set(globals().keys())
    return sorted(base | set(_SUBMODULES.keys()) | set(_SYMBOLS.keys()))


# Type-checker-only direct imports (no runtime side-effects)
if TYPE_CHECKING:
    from .block import Block  # noqa: F401
    from .header import Header  # noqa: F401
    from .params import ChainParams  # noqa: F401
    from .proof import QuantumProofRef  # noqa: F401
    from .proof import AIProofRef, HashShare, StorageHeartbeat, VDFProofRef
    from .receipt import Receipt  # noqa: F401
    from .tx import Tx  # noqa: F401
