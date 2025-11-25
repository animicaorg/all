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
    """Readable wrapper for sender ids."""
    name: str

    @property
    def key(self) -> bytes:
        # Sender id is arbitrary bytes; using the ASCII name keeps tests readable.
        return self.name.encode("ascii")


class FakeClock:
    """
    Deterministic, manually-advanced clock.

    BanList takes a `clock` callable; we inject this so we can assert on
    spam_ban_s and window_s behavior without real sleeps.
    """

    def __init__(self, start: float = 0.0) -> None:
        self._t = float(start)

    def __call__(self) -> float:
        return self._t

    def advance(self, seconds: float) -> None:
        self._t += float(seconds)


def _banlist_with_spam_policy(
    *,
    spam_ban_s: int,
    window_s: int = 10,
    max_rejects_in_window: int = 5,
) -> tuple[BanList, FakeClock, BanPolicy]:
    clock = FakeClock(start=0.0)
    policy = BanPolicy()
    policy.spam_ban_s = spam_ban_s
    policy.window_s = window_s
    policy.max_rejects_in_window = max_rejects_in_window
    bl = BanList(policy=policy, clock=clock)
    return bl, clock, policy


# ---------------------------------------------------------------------------
# Direct spam ban: "oversize/malformed burst" → ban_for_spam()
# ---------------------------------------------------------------------------


def test_direct_spam_ban_respects_configured_interval() -> None:
    """
    A single "abusive" event (e.g., oversize/malformed burst classified at
    a higher level) can directly call ban_for_spam(sender).

    We expect:
      - Sender becomes banned immediately.
      - They remain banned for at least spam_ban_s seconds.
      - The ban lifts after the DoS interval passes.
    """
    spam_ban_s = 30
    banlist, clock, policy = _banlist_with_spam_policy(spam_ban_s=spam_ban_s)

    alice = FakeSender("alice")

    # Initially not banned.
    assert not banlist.is_banned(alice.key)

    # Simulate an oversize/malformed burst → direct spam ban.
    banlist.ban_for_spam(alice.key)
    assert banlist.is_banned(alice.key)

    # Just before expiry, still banned.
    clock.advance(spam_ban_s - 1)
    assert banlist.is_banned(alice.key)

    # After the configured DoS interval, ban should lift.
    clock.advance(2)
    assert not banlist.is_banned(alice.key)


# ---------------------------------------------------------------------------
# Rolling rejects: bursts of "bad" txs → record_reject() auto-bans
# ---------------------------------------------------------------------------


def test_reject_burst_triggers_spam_ban_via_record_reject() -> None:
    """
    Oversize/malformed tx bursts are modeled as "reject" events.

    When a sender triggers `max_rejects_in_window` rejects inside window_s,
    BanList.record_reject() must escalate them to a spam ban using the
    configured spam_ban_s interval.
    """
    spam_ban_s = 40
    window_s = 10
    max_rejects = 3

    banlist, clock, policy = _banlist_with_spam_policy(
        spam_ban_s=spam_ban_s,
        window_s=window_s,
        max_rejects_in_window=max_rejects,
    )

    bob = FakeSender("bob")

    # Three quick rejects within the same window should trigger ban_for_spam().
    for _ in range(max_rejects):
        banlist.record_reject(bob.key)

    assert banlist.is_banned(bob.key)

    # Advance past spam_ban_s → ban should lift.
    clock.advance(spam_ban_s + 1)
    assert not banlist.is_banned(bob.key)


def test_rejects_spread_across_windows_do_not_immediately_ban() -> None:
    """
    If rejects are spread across windows such that the rolling counter
    resets, a sender should not be banned until they exceed the threshold
    *within a single window*.

    This simulates a slow trickle of malformed txs that never quite forms
    a DoS burst.
    """
    spam_ban_s = 60
    window_s = 5
    max_rejects = 3

    banlist, clock, policy = _banlist_with_spam_policy(
        spam_ban_s=spam_ban_s,
        window_s=window_s,
        max_rejects_in_window=max_rejects,
    )

    carol = FakeSender("carol")

    # First window: 2 rejects (below threshold).
    banlist.record_reject(carol.key)
    clock.advance(1)
    banlist.record_reject(carol.key)
    assert not banlist.is_banned(carol.key)

    # Advance beyond window_s → counters reset.
    clock.advance(window_s + 1)

    # Second window: again 2 rejects (still below threshold).
    banlist.record_reject(carol.key)
    banlist.record_reject(carol.key)
    assert not banlist.is_banned(carol.key)

    # Now a third reject in the same window pushes us over the threshold.
    banlist.record_reject(carol.key)
    assert banlist.is_banned(carol.key)


# ---------------------------------------------------------------------------
# Spam bans remain sender-scoped
# ---------------------------------------------------------------------------


def test_spam_ban_does_not_affect_other_senders() -> None:
    spam_ban_s = 25
    banlist, clock, policy = _banlist_with_spam_policy(spam_ban_s=spam_ban_s)

    dave = FakeSender("dave")
    erin = FakeSender("erin")

    # Dave spams; Erin behaves.
    banlist.ban_for_spam(dave.key)

    assert banlist.is_banned(dave.key)
    assert not banlist.is_banned(erin.key)

    # After the DoS interval, bans should lift and both be clear.
    clock.advance(spam_ban_s + 1)
    assert not banlist.is_banned(dave.key)
    assert not banlist.is_banned(erin.key)
