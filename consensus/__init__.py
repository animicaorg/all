"""
Animica Consensus (PoIES)

This package implements the consensus-side primitives for PoIES:
- acceptance scalar S = H(u) + Σψ
- caps/fairness & α-tuning
- fractional retargeting of Θ
- fork choice & nullifier windows
All submodules are deterministic and pure (no network I/O).

Re-exports:
    __version__
    errors, math, policy, caps, scorer, difficulty, fork_choice,
    interfaces, validator, nullifiers, share_receipts, alpha_tuner,
    window, state
"""

# Re-export key submodules for ergonomic imports
from . import (alpha_tuner, caps, difficulty, errors, fork_choice, interfaces,
               math, nullifiers, scorer, share_receipts, state, validator,
               window)
from .version import __version__  # defined in consensus/version.py

__all__ = [
    "__version__",
    # submodules
    "errors",
    "math",
    "caps",
    "scorer",
    "difficulty",
    "fork_choice",
    "interfaces",
    "validator",
    "nullifiers",
    "share_receipts",
    "alpha_tuner",
    "window",
    "state",
]
