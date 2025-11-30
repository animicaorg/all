"""
Animica | proofs.attestations.tee

Thin namespace package for TEE attestation verifiers used by proofs/ai.py:

Submodules (lazy-loaded via __getattr__):
- common   : shared structures, measurement binding, policy flags
- sgx      : Intel SGX/TDX quote parse/verify (PCK chain, QE identity)
- sev_snp  : AMD SEV-SNP report parse/verify (ARK/ASK certs, TCB)
- cca      : Arm CCA Realm attestation token verify (COSE/CCA roots)

This module avoids importing heavy crypto stacks at package import time by
using PEP 562 lazy attribute loading. Static type checkers still see the
symbols thanks to TYPE_CHECKING imports.
"""

from __future__ import annotations

from importlib import import_module
from typing import TYPE_CHECKING, Dict

__all__ = ["common", "sgx", "sev_snp", "cca"]

# Map attribute → fully-qualified module for lazy loading.
_MODULES: Dict[str, str] = {
    "common": "proofs.attestations.tee.common",
    "sgx": "proofs.attestations.tee.sgx",
    "sev_snp": "proofs.attestations.tee.sev_snp",
    "cca": "proofs.attestations.tee.cca",
}

if TYPE_CHECKING:
    # Static import hints for type checkers (no runtime cost).
    from . import cca as cca  # noqa: F401
    from . import common as common  # noqa: F401
    from . import sev_snp as sev_snp  # noqa: F401
    from . import sgx as sgx  # noqa: F401


def __getattr__(name: str):
    """Lazy import submodules on first attribute access."""
    try:
        target = _MODULES[name]
    except KeyError as e:  # pragma: no cover - standard attribute error path
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}") from e
    mod = import_module(target)
    globals()[name] = mod  # cache for future lookups
    return mod


def __dir__():
    # Expose lazily-loadable names as if they were present.
    return sorted(list(globals().keys()) + list(_MODULES.keys()))
