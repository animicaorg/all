# Copyright (c) Animica.
# SPDX-License-Identifier: MIT
"""
randomness.commit_reveal
========================

Commit–reveal subpackage for the randomness beacon.

This package groups the higher-level helpers and state transitions
for the beacon's commit and reveal lifecycle. It intentionally
re-exports the canonical dataclasses used by the protocol so
callers can import them from a stable path.

Typical flow (block-time driven):
    1) Build and submit a commitment during the round's **commit** window.
    2) Reveal the preimage during the **reveal** window.
    3) (Optionally) verify a VDF over the mixed transcript.

Submodules expected in this package:
    - commit.py   : helpers to construct/validate commitments.
    - reveal.py   : helpers to validate reveals and derive beacon inputs.
    - mix.py      : (optional) transcript/mixing utilities.

Only common types are re-exported here to avoid import cycles.
"""

from __future__ import annotations

# Re-export protocol types for convenience.
try:
    from randomness.types.core import (CommitRecord,  # noqa: F401
                                       RevealRecord, RoundId)
except Exception:  # pragma: no cover
    # During bootstrap or partial installs these may be unavailable.
    RoundId = int  # type: ignore

    class CommitRecord:  # type: ignore
        pass

    class RevealRecord:  # type: ignore
        pass


# Surface package version if available.
try:  # pragma: no cover
    from randomness.version import __version__ as __version__  # noqa: F401
except Exception:  # pragma: no cover
    __version__ = "0.0.0"

__all__ = [
    "RoundId",
    "CommitRecord",
    "RevealRecord",
    "__version__",
]
