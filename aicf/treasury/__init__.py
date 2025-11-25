from __future__ import annotations
"""
aicf.treasury
=============

Treasury-facing package for AICF (AI/Quantum Compute Fund) accounting.

This namespace groups modules that interface with the protocol's treasury
mechanisms—e.g., crediting provider payouts, handling slashes/clawbacks,
and performing epoch-based settlements—while remaining deterministic and
side-effect free at the import level.

Submodules are expected to provide pure functions over explicit inputs and
to leave persistence and on-chain state mutations to higher-level bridges
(e.g., aicf.economics.settlement and integration hooks).

This file intentionally exports nothing yet; concrete primitives live in
the sibling modules (e.g., settlement helpers, ledger builders) and are
re-exported here once stabilized.
"""


# Semantic version of this subpackage (follows aicf.__version__ cadence).
__treasury_api__ = "0.1.0"

__all__: list[str] = []
