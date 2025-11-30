"""
vm_pkg.compiler — tiny compiler surface for the in-browser simulator.

This package exposes a minimal, stable facade around the trimmed Python-VM
compiler bits that ship with the studio-wasm bundle. It is intentionally small:

Modules
-------
- ir            : IR data structures used by the interpreter.
- encode        : canonical (de)serialization of IR.
- typecheck     : lightweight validator for example contracts.
- gas_estimator : coarse static upper-bound estimator.

Convenience functions
---------------------
- encode_ir(module) -> bytes
- decode_ir(blob)   -> ir.Module
- validate(module)  -> None (raises on error)
- estimate_gas(module) -> int

Imports are performed inside each helper to avoid import-time failures if the
environment only loads a subset during incremental builds.
"""

from __future__ import annotations

# Re-export submodules for direct access.
from . import encode as encode  # noqa: F401
from . import gas_estimator as gas_estimator  # noqa: F401
from . import ir as ir  # noqa: F401
from . import typecheck as typecheck  # noqa: F401

__all__ = [
    "ir",
    "encode",
    "typecheck",
    "gas_estimator",
    "encode_ir",
    "decode_ir",
    "validate",
    "estimate_gas",
]


# -------- Convenience wrappers (import-on-call) --------


def encode_ir(module) -> bytes:
    """
    Serialize an IR module to canonical bytes.
    """
    # Local import to keep module load light in the browser.
    from .encode import encode_ir as _encode_ir

    return _encode_ir(module)


def decode_ir(blob: bytes):
    """
    Parse canonical bytes into an IR module.
    """
    from .encode import decode_ir as _decode_ir

    return _decode_ir(blob)


def validate(module) -> None:
    """
    Run the lightweight validator on the given IR module.
    Raises ValidationError (or a subtype) on problems.
    """
    # Accept either validate_module() or typecheck() depending on implementation.
    try:
        from .typecheck import validate_module as _validate
    except Exception:  # pragma: no cover - fallback alias
        from .typecheck import typecheck as _validate  # type: ignore
    _validate(module)


def estimate_gas(module) -> int:
    """
    Return a coarse upper-bound gas estimate for the module.
    """
    from .gas_estimator import estimate as _estimate

    return int(_estimate(module))
