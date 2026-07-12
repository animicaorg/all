"""Recovery-destination policy for clawback / incident migrations.

Operator directive (2026-07-12): **every FUTURE clawback migration credits the
foundation treasury**, not fresh per-victim recovery addresses. This module is
the single source of that destination so no future migration hardcodes a
divergent literal.

HARD RULES
==========
1. This applies to migrations that have **not yet activated**. A migration whose
   activation height is at or below the current chain head has already been
   applied identically by every node; changing its destination retroactively
   does NOT move any funds — it only makes a re-sync/reorg compute a different
   state and silently fork account balances (root-commitment does not reject a
   pre-40,000 mismatch). Never edit an activated migration's destination.
   In particular ``clawback_2026_07`` activated at mainnet height 39,584 and is
   immutable; its recovered funds already sit at the two victim recovery
   addresses. Do not repoint it here or anywhere.
2. The destination is derived from the canonical
   ``consensus.rewards.FOUNDATION_TREASURY_ADDRESS`` (the same address the
   reward split pays), decoded to its 32-byte account key at import. It cannot
   drift from that constant.
3. A clawback only ever moves REAL, on-chain funds an incident left in an
   attacker/collection account. It is not a tool to mint value: never "claw
   back" phantom/never-executed balances into the treasury — that is
   counterfeit issuance, not recovery.
"""

from __future__ import annotations

from consensus.rewards import FOUNDATION_TREASURY_ADDRESS
from core.utils.address import address_to_bytes

# Canonical bech32m destination for all future clawback recoveries.
FUTURE_CLAWBACK_RECOVERY_ADDRESS: str = FOUNDATION_TREASURY_ADDRESS

# 32-byte on-chain account key of that address (what a migration credits).
# Decoded from the bech32m constant so it stays in lockstep with the reward
# split; the literal below is a documented cross-check, asserted at import.
FUTURE_CLAWBACK_RECOVERY_KEY: bytes = address_to_bytes(FUTURE_CLAWBACK_RECOVERY_ADDRESS)

_EXPECTED_KEY_HEX = "0de5187830c1493cb6ce5341e2cf23901084cffd6cff37e404c727a301036229"

if len(FUTURE_CLAWBACK_RECOVERY_KEY) != 32:
    raise AssertionError(
        "foundation treasury did not decode to a 32-byte account key: "
        f"{len(FUTURE_CLAWBACK_RECOVERY_KEY)} bytes"
    )
if FUTURE_CLAWBACK_RECOVERY_KEY.hex() != _EXPECTED_KEY_HEX:
    raise AssertionError(
        "foundation treasury account key drifted from the audited value: "
        f"{FUTURE_CLAWBACK_RECOVERY_KEY.hex()} != {_EXPECTED_KEY_HEX}"
    )

__all__ = [
    "FUTURE_CLAWBACK_RECOVERY_ADDRESS",
    "FUTURE_CLAWBACK_RECOVERY_KEY",
]
