from __future__ import annotations

"""
aicf.economics
==============

Lightweight package marker for AICF economics-related helpers.

This subpackage will host payout and slashing formulas, reward-split
utilities, and fee-conversion helpers. We keep imports minimal here to
avoid creating heavy dependency chains at import time.

Re-exports:
- __version__ (from aicf.version)
- (best-effort) Payout, RewardSplit from aicf.aitypes.payout for convenience
"""


from typing import List

# Surface the module version alongside economics helpers
try:
    from ..version import __version__  # type: ignore
except Exception:  # pragma: no cover
    __version__ = "0.0.0+unknown"

__all__: List[str] = ["__version__"]

# Convenience re-exports (optional; guarded to avoid import-time errors)
try:  # pragma: no cover - optional convenience
    from aicf.aitypes.payout import Payout, RewardSplit  # type: ignore

    __all__ += ["Payout", "RewardSplit"]
except Exception:
    # Types module may not be available in some slim builds/tests.
    pass
