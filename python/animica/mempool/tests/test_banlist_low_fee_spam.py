from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import pytest

banlist_mod = pytest.importorskip(
    "mempool.banlist", reason="mempool.banlist module not found"
)

BanPolicy = banlist_mod.BanPolicy
BanList = banlist_mod.BanList


@dataclass
class FakeSender:
    """Helper just to keep sender identities readable in assertions."""
    name: str

    @property
    def key(self) -> bytes:
        # Use the name bytes as the sender key; any hashable bytes work.
        return self.name.encode("ascii")


class FakeClock:
    """
    Deterministic clock we can manually advance.

    BanList takes a `clock` callable; we inject this to make ban durations
    and lift times testable without sleeping.
    """

    def __init__(self, start: float = 0.0) -> None:
        self._t = float(start)

    def __call__(self) -> float:
        return self._t

    def advance(self, seconds: float) -> None:
        self._t += float(seconds)


def _banlist_with_low_fee_policy(low_fee_ban_s: int) -> tuple[BanList, FakeClock, BanPolicy]:
    clock = FakeClock(start=0.0)
    policy = BanPolicy()
    policy.low_fee_ban_s = low_fee_ban_s
    # Keep spam-related knobs at defaults; we only test low-fee behavior here.
    bl = BanList(policy=policy, clock=clock)
    return bl, clock, policy


# ---------------------------------------------------------------------------
# Single low-fee ban: duration & lift time
# ---------------------------------------------------------------------------


def test_single_low_fee_ban_has_expected_duration_and_lifts() -> None:
    """
    A single low-fee ban should:

    - Mark the sender as banned immediately.
    - Keep them banned for at least `low_fee_ban_s` seconds.
    - Lift the ban once the clock passes the ban horizon.
    """
    low_fee_ban_s = 10
    banlist, clock, policy = _banlist_with_low_fee_policy(low_fee_ban_s)

    alice = FakeSender("alice")

    # Initially not banned.
    assert not banlist.is_banned(alice.key)

    # One low-fee event → ban.
    banlist.ban_for_low_fee(alice.key)
    assert banlist.is_banned(alice.key)

    # Just before expiry: still banned.
    clock.advance(low_fee_ban_s - 1)
    assert banlist.is_banned(alice.key)

    # After expiry horizon: ban should lift.
    clock.advance(2)
    assert not banlist.is_banned(alice.key)


# ---------------------------------------------------------------------------
# Repeated low-fee bans: extend ban horizon
# ---------------------------------------------------------------------------


def test_repeated_low_fee_bans_extend_ban_horizon() -> None:
    """
    Repeated low-fee submits should extend the ban horizon.

    Given low_fee_ban_s = 10:
      - First ban at t=0 → banned until t=10.
      - Second ban at t=5 → banned until t=15 (max(old_until, now+10)).
    """
    low_fee_ban_s = 10
    banlist, clock, policy = _banlist_with_low_fee_policy(low_fee_ban_s)

    bob = FakeSender("bob")

    # First ban at t = 0 → until ≈ 10.
    banlist.ban_for_low_fee(bob.key)
    assert banlist.is_banned(bob.key)

    # Advance to t = 5, still banned.
    clock.advance(5)
    assert banlist.is_banned(bob.key)

    # Second low-fee event at t = 5 → horizon should be pushed out.
    banlist.ban_for_low_fee(bob.key)

    # Up to just before t = 15: still banned.
    clock.advance(9)  # now at t = 14
    assert banlist.is_banned(bob.key)

    # Past t = 15: ban should have lifted.
    clock.advance(2)  # now at t = 16
    assert not banlist.is_banned(bob.key)


# ---------------------------------------------------------------------------
# Low-fee bans do not affect other senders
# ---------------------------------------------------------------------------


def test_low_fee_ban_is_sender_scoped() -> None:
    """
    A low-fee ban should be scoped to the offending sender; other senders
    remain unaffected.
    """
    low_fee_ban_s = 20
    banlist, clock, policy = _banlist_with_low_fee_policy(low_fee_ban_s)

    alice = FakeSender("alice")
    carol = FakeSender("carol")

    banlist.ban_for_low_fee(alice.key)

    # Alice is banned; Carol is not.
    assert banlist.is_banned(alice.key)
    assert not banlist.is_banned(carol.key)

    # After ban horizon passes, nobody is banned.
    clock.advance(low_fee_ban_s + 1)
    assert not banlist.is_banned(alice.key)
    assert not banlist.is_banned(carol.key)
