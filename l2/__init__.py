"""Animica L2 — ANM-native, trust-minimized Layer 2 for near-instant payments.

The canonical Animica L1 (``core/``, ``execution/``, ``consensus/``) remains the
settlement and security layer. This package is *additive*: importing it does not
alter L1 consensus. L1 anchoring of L2 state roots / data-availability blobs is
gated behind a height-activated fork (see :mod:`l2.constants`) so nodes that do
not understand 10.0.0 anchoring fail loudly rather than silently disagree.

Security posture of 10.0.0 (see docs/l2/SECURITY_ASSUMPTIONS.md):

* ANM is never minted on L2. The withdrawable-<=-locked invariant is enforced by
  :mod:`l2.bridge` and asserted after every batch.
* The settlement security mode is explicit — ``VALIDITY`` (state transitions are
  re-executed and checked deterministically by any verifier), ``OPTIMISTIC``
  (bonded sequencer + challenge window + fraud proofs), or ``DEV``. We never
  label a sequencer-operated component "trustless"; the forced-exit escape hatch
  is what bounds sequencer power.
* The proof system is pluggable (:mod:`l2.proof`). 10.0.0 ships a real
  re-execution validity backend; a succinct/ZK backend can replace it behind the
  same ``ProofBackend`` interface without changing tx/state formats.
"""

from __future__ import annotations

__all__ = ["__version__", "PROTOCOL_VERSION"]

__version__ = "10.0.0"
# Bumped independently of the package version when the wire/state formats change
# in a consensus-relevant way; embedded in the signing domain (see l2.tx).
PROTOCOL_VERSION = 1
