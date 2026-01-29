"""
randomness.beacon
=================

Beacon package marker & light exports.

This subpackage groups high-level interfaces for Animica's randomness beacon,
which combines commit–reveal aggregation with a VDF and (optionally) mixes in
QRNG bytes. Downstream modules can import the canonical output type directly
from here:

    from randomness.beacon import BeaconOut

Lower layers live in:
- randomness.commit_reveal
- randomness.vdf
- randomness.qrng
- randomness.types
"""

from __future__ import annotations

# Re-export the canonical beacon output type so callers don't need to know
# the internal path.
from randomness.types.core import BeaconOut  # noqa: F401

__all__ = ["BeaconOut"]
