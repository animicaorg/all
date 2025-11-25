"""
omni_sdk.filestore
==================

Lightweight helpers for working with temporary, content-addressed, and
write-once artifact directories used by the Animica SDK tooling (e.g. local
compiles, bundles, proofs, and receipts).

Public surface (re-exported lazily from :mod:`omni_sdk.filestore.tempdir`):

- TempDir: context-managed temporary work directory with safe cleanup.
- temp_dir(): convenience context manager yielding a TempDir instance.
- ensure_dir(path): create a directory (and parents) if missing; safe in races.
- atomic_write(path, data, mode=0o644): write bytes atomically via temp file + rename.
- content_addressed_path(root, data, algo="sha3-256"): compute path by digest bucket.
- write_blob_ca(root, data, *, algo="sha3-256"): store bytes under content-address path.

Implementation note
-------------------
We lazy-import submodules to keep import overhead minimal and avoid import
order pitfalls. If you add new helpers to ``tempdir.py``, they will be
automatically exposed here via ``__getattr__``/``__dir__``.
"""

from __future__ import annotations

from importlib import import_module
from typing import Any, List

__all__: List[str] = [
    # These names are provided by filestore.tempdir and re-exported here.
    "TempDir",
    "temp_dir",
    "ensure_dir",
    "atomic_write",
    "content_addressed_path",
    "write_blob_ca",
]

_SUBMODULE = "omni_sdk.filestore.tempdir"


def __getattr__(name: str) -> Any:  # PEP 562: module-level lazy attributes
    if name in __all__:
        mod = import_module(_SUBMODULE)
        try:
            return getattr(mod, name)
        except AttributeError as e:  # pragma: no cover
            raise AttributeError(f"omni_sdk.filestore has no attribute {name!r}") from e
    # Allow access to the submodule itself: omni_sdk.filestore.tempdir
    if name == "tempdir":
        return import_module(_SUBMODULE)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def __dir__() -> List[str]:  # helps IDEs and dir()
    std = list(globals().keys())
    try:
        mod = import_module(_SUBMODULE)
        public = [n for n in getattr(mod, "__all__", []) or dir(mod) if not n.startswith("_")]
        return sorted(set(std + public + __all__))
    except Exception:  # pragma: no cover
        return sorted(set(std + __all__))
