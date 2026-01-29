"""
Randomness beacon — types package

This subpackage collects typed primitives and dataclasses used across the
beacon pipeline. The canonical modules (added in subsequent files) are:

  • round        — RoundId, RoundPhase, RoundWindow
  • participant  — ParticipantId
  • records      — CommitRecord, RevealRecord
  • vdf          — VDFParams, VDFProof
  • mix          — MixInput, MixOutput

We re-export commonly used symbols from here for convenience:
    from randomness.types import RoundId, CommitRecord, VDFProof

The guarded imports below make the package import-safe even if individual
modules have not been created yet during incremental development.
"""

from __future__ import annotations

# ---- Re-exports (guarded to avoid import errors during bring-up) ----

try:
    from .round import RoundId, RoundPhase, RoundWindow  # type: ignore[F401]
except Exception:
    pass

try:
    from .participant import ParticipantId  # type: ignore[F401]
except Exception:
    pass

try:
    from .records import CommitRecord, RevealRecord  # type: ignore[F401]
except Exception:
    pass

try:
    from .vdf import VDFParams, VDFProof  # type: ignore[F401]
except Exception:
    pass

try:
    from .mix import MixInput, MixOutput  # type: ignore[F401]
except Exception:
    pass


# Build __all__ dynamically from what actually resolved into globals()
__all__ = [
    name
    for name in (
        "RoundId",
        "RoundPhase",
        "RoundWindow",
        "ParticipantId",
        "CommitRecord",
        "RevealRecord",
        "VDFParams",
        "VDFProof",
        "MixInput",
        "MixOutput",
    )
    if name in globals()
]
