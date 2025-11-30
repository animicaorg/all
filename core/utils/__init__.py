"""
Animica — core.utils
--------------------

Utility toolkit used across the node (pure-stdlib here to avoid import cycles).

This package *lazily* exposes its submodules so importing `core.utils` is cheap:

    from core import utils
    h = utils.hash.sha3_256(b"hello")
    s = utils.serialization.to_canonical_json({"a": 1})
    bx = utils.bytes.ensure_len(b"\x00", 1)

Submodules (planned/implemented elsewhere in this repo):
- `bytes`          : hex/bytes/bech32 helpers, length/typing guards
- `hash`           : SHA3-512/256, optional BLAKE3, tree-hash helpers
- `merkle`         : canonical Merkle (used for state/proofs roots)
- `serialization`  : canonical JSON helpers (sorting, bigint-safe)

Notes
-----
- We intentionally *do not* import heavy optional deps here.
- Names like `bytes` and `hash` shadow Python builtins if imported directly;
  prefer module-qualified access (`utils.bytes`, `utils.hash`) or the aliases
  below (`bytes_utils`, `hash_utils`) in user code.

This module is typed to keep mypy/pyright happy without importing submodules at
import time.
"""

from __future__ import annotations

from importlib import import_module
from types import ModuleType
from typing import TYPE_CHECKING, Any, Dict, Iterable, List

__all__: List[str] = [
    # primary submodule names
    "bytes",
    "hash",
    "merkle",
    "serialization",
    # convenient aliases that don't shadow builtins
    "bytes_utils",
    "hash_utils",
]

# Internal registry of lazily-loadable submodules
_SUBMODS: Dict[str, str] = {
    "bytes": "core.utils.bytes",
    "hash": "core.utils.hash",
    "merkle": "core.utils.merkle",
    "serialization": "core.utils.serialization",
}

# Aliases → underlying submodule
_ALIASES: Dict[str, str] = {
    "bytes_utils": "bytes",
    "hash_utils": "hash",
}

if TYPE_CHECKING:  # pragma: no cover - type-checking only
    # These imports are only for static type checkers; they don't execute at runtime.
    from . import bytes as bytes  # type: ignore
    from . import hash as hash  # type: ignore
    from . import merkle as merkle
    from . import serialization as serialization

    bytes_utils: ModuleType
    hash_utils: ModuleType


def __getattr__(name: str) -> Any:
    """
    Lazy attribute resolution for submodules and their aliases.
    This keeps `import core.utils` near-zero cost.
    """
    # Resolve alias → canonical name
    canonical = _ALIASES.get(name, name)

    if canonical in _SUBMODS:
        mod = import_module(_SUBMODS[canonical])
        # Cache under both the canonical name and any alias used
        globals()[canonical] = mod
        if name != canonical:
            globals()[name] = mod
        return mod

    raise AttributeError(f"module 'core.utils' has no attribute '{name}'")


def __dir__() -> Iterable[str]:  # pragma: no cover - sugar for REPLs
    return sorted(set(list(globals().keys()) + list(__all__)))


# Small sanity self-test helper (lightweight, no imports unless asked)
def _selftest() -> Dict[str, bool]:  # pragma: no cover - debug helper
    """
    Return a map of submodule -> availability without importing everything
    eagerly on module import. Useful in diagnostics.
    """
    out: Dict[str, bool] = {}
    for k in _SUBMODS:
        try:
            import_module(_SUBMODS[k])
            out[k] = True
        except Exception:
            out[k] = False
    return out
