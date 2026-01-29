"""
Animica • Data Availability — Namespaced Merkle Tree (NMT)

This subpackage implements a binary Namespaced Merkle Tree used to commit to
data-availability blob shares. It provides:

  • namespace.py : Namespace id/range types and validation
  • node.py      : Internal node structure (left/right, namespace range)
  • tree.py      : Incremental tree builder (append/finalize) producing NMT roots
  • proofs.py    : Inclusion & namespace-range proof builders
  • commit.py    : Convenience helpers to compute DA (NMT) roots from leaves
  • codec.py     : Leaf serialization (namespace || len || data)
  • verify.py    : Fast proof verification (inclusion & range)
  • indices.py   : Index math, mapping between leaf indices and share positions

Wire encodings and single-byte domain tags are defined in:
    da/schemas/nmt.cddl
and hashing primitives are shared via:
    da.utils.hash

Typical usage
-------------
    from da.nmt import tree, codec, namespace

    ns = namespace.NamespaceId(24)              # pick a namespace id
    leaf = codec.encode_leaf(ns, b"hello")      # serialize a leaf
    t = tree.NMT()
    t.append_encoded(leaf)
    root = t.finalize()

This package lazily loads its submodules to keep imports lightweight.
"""

from __future__ import annotations

from importlib import import_module
from typing import Any

# Re-export version from the top-level DA package.
try:
    from ..version import __version__  # type: ignore[F401]
except Exception:  # pragma: no cover
    __version__ = "0.0.0+unknown"

# Lazily exposed submodules
_SUBMODULES = (
    "namespace",
    "node",
    "tree",
    "proofs",
    "commit",
    "codec",
    "verify",
    "indices",
)


def __getattr__(name: str) -> Any:
    """
    Lazily resolve well-known submodules, e.g. `da.nmt.tree`.
    """
    if name in _SUBMODULES:
        try:
            return import_module(f"{__name__}.{name}")
        except ModuleNotFoundError as e:  # pragma: no cover
            raise AttributeError(
                f"Optional submodule 'da.nmt.{name}' is not available."
            ) from e
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def __dir__() -> list[str]:
    return sorted(list(globals().keys()) + list(_SUBMODULES))


__all__ = list(_SUBMODULES)
