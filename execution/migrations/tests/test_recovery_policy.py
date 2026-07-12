"""Future clawbacks must credit the foundation treasury (operator directive).

Also guards the invariant that the past, already-activated ANM-2026-07 clawback
is NOT repointed by the policy — its victim recovery addresses must stay
distinct from the future-clawback destination.
"""
from __future__ import annotations

from consensus.rewards import FOUNDATION_TREASURY_ADDRESS
from core.utils.address import address_to_bytes
from execution.migrations import _recovery_policy as pol


def test_future_clawback_destination_is_foundation_treasury():
    assert pol.FUTURE_CLAWBACK_RECOVERY_ADDRESS == FOUNDATION_TREASURY_ADDRESS
    assert pol.FUTURE_CLAWBACK_RECOVERY_KEY == address_to_bytes(FOUNDATION_TREASURY_ADDRESS)
    assert len(pol.FUTURE_CLAWBACK_RECOVERY_KEY) == 32
    assert (
        pol.FUTURE_CLAWBACK_RECOVERY_KEY.hex()
        == "0de5187830c1493cb6ce5341e2cf23901084cffd6cff37e404c727a301036229"
    )


def test_policy_does_not_touch_the_activated_2026_07_clawback():
    from execution.migrations import clawback_2026_07 as cb

    # The past clawback still points at its two victim recovery digests, and the
    # future-clawback destination is none of them (policy is forward-only).
    victim_keys = {dig for dig, _ in cb._RECOVERIES}
    assert pol.FUTURE_CLAWBACK_RECOVERY_KEY not in victim_keys
    # And the attacker/collection account is untouched by the policy.
    assert pol.FUTURE_CLAWBACK_RECOVERY_KEY != cb._THIEF


if __name__ == "__main__":
    import pytest, sys
    sys.exit(pytest.main([__file__, "-q"]))
