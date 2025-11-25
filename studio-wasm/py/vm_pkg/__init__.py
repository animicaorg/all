"""
Animica Python VM (browser subset) — vm_pkg
===========================================

This package is the **Pyodide-compatible subset** of the Animica Python VM, used by
`@animica/studio-wasm` to compile and simulate contracts entirely in the browser.

It is generated during the build by `studio-wasm/scripts/sync_vm_py.py`, which copies a
curated set of modules from the source `vm_py/` tree, rewrites imports to `vm_pkg.*`,
and writes a small manifest (`_sync_manifest.json`) including the upstream VM version.

The subset intentionally omits host-specific pieces (e.g., sandbox/syscalls/state
adapters). Contract-facing APIs (ABI, storage, events, hashing) and the interpreter,
IR encoder/decoder, and minimal typechecking/gas-estimation are included.

Do not edit files in this directory by hand; changes will be overwritten by the sync
script. Extend or patch the upstream `vm_py/` package instead.
"""

from __future__ import annotations

from importlib import resources
import json
from typing import Any, Dict

# Re-exports for convenience
from . import runtime as runtime  # engine, gasmeter, context, storage/events/hash/abi/random
from . import stdlib as stdlib    # contract-facing stdlib surface (browser-safe)
from . import compiler as compiler  # IR types/encode/typecheck/gas_estimator
from . import errors as errors    # VmError, ValidationError, OOG, Revert

__all__ = ["runtime", "stdlib", "compiler", "errors", "get_version", "__version__"]


def _read_manifest_version() -> str:
    """
    Best-effort read of the upstream vm_py version from the sync manifest that the
    build script drops alongside this package. Falls back to '0.0.0' if absent.
    """
    try:
        # The sync script writes a text file into this package namespace
        with resources.files(__package__).joinpath("_sync_manifest.json").open("rb") as f:
            data: Dict[str, Any] = json.load(f)
            v = data.get("vmVersion")
            return str(v) if v else "0.0.0"
    except Exception:
        return "0.0.0"


def get_version() -> str:
    """Return the upstream vm_py version this subset was generated from."""
    return __version__


__version__ = _read_manifest_version()
