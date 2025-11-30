"""
omni_sdk.proofs
===============

Developer-facing helpers for assembling and validating proof envelopes used by
the Animica node:

- :mod:`omni_sdk.proofs.hashshare` — build/verify HashShare proofs locally
  for devnets and diagnostics.
- :mod:`omni_sdk.proofs.ai` — assemble AIProof reference objects from
  provider outputs/attestations.
- :mod:`omni_sdk.proofs.quantum` — assemble QuantumProof reference objects
  from provider attestations and trap outcomes.

This package provides *thin* utilities for client tooling and tests. The node's
`proofs/` module remains the source of truth for consensus verification.

Quick start
-----------
    from omni_sdk.proofs import build_hashshare, verify_hashshare

    hs = build_hashshare(header_template=hdr, nonce=b"\x00"*8, mix_seed=b"...")
    ok = verify_hashshare(hs, target_ratio=0.5)

    from omni_sdk.proofs import assemble_ai_proof
    ai_proof = assemble_ai_proof(outputs=..., attestation=..., qos=...)

The exact function signatures are documented in their respective modules.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

# Re-export version if available
try:  # pragma: no cover
    from ..version import __version__  # type: ignore
except Exception:  # pragma: no cover
    __version__ = "0.0.0"

__all__ = [
    # submodules
    "hashshare",
    "ai",
    "quantum",
    # common function re-exports (lazy)
    "build_hashshare",
    "verify_hashshare",
    "assemble_ai_proof",
    "assemble_quantum_proof",
    "__version__",
]

if TYPE_CHECKING:
    # For type checkers, expose real symbols
    from . import ai, hashshare, quantum  # type: ignore
    from .ai import assemble_ai_proof  # type: ignore
    from .hashshare import build_hashshare, verify_hashshare  # type: ignore
    from .quantum import assemble_quantum_proof  # type: ignore
else:
    # Lazy attribute loading to keep import-time deps light
    def __getattr__(name: str):
        if name in ("hashshare", "ai", "quantum"):
            module = __import__(f"{__name__}.{name}", fromlist=[name])
            return module
        if name in ("build_hashshare", "verify_hashshare"):
            from .hashshare import (build_hashshare,  # type: ignore
                                    verify_hashshare)

            return {
                "build_hashshare": build_hashshare,
                "verify_hashshare": verify_hashshare,
            }[name]
        if name == "assemble_ai_proof":
            from .ai import assemble_ai_proof  # type: ignore

            return assemble_ai_proof
        if name == "assemble_quantum_proof":
            from .quantum import assemble_quantum_proof  # type: ignore

            return assemble_quantum_proof
        raise AttributeError(name)
