"""
randomness.commit_reveal.slashing
=================================

Optional slashing hooks for the commit→reveal beacon.

This module **does not** modify balances or stake by itself. Instead, it exposes
typed hooks that higher layers (treasury/staking, governance, etc.) can plug
into. If slashing is disabled in params, the helpers no-op.

Typical usage
-------------
- Register a sink that implements ``SlashSink`` once during node startup.
- Call ``record_miss(...)`` when a participant failed to reveal within the
  reveal window.
- Call ``record_bad_reveal(...)`` when a participant revealed something that
  does not match their prior commitment.

Penalty sizing
--------------
We provide a convenience function ``suggest_penalty`` based on basis-point
parameters from ``commit_reveal.params``. Adapters are free to ignore it and
apply their own policy.

This module is deliberately dependency-light: it only depends on local types and
does not import any treasury/staking code.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Optional, Protocol

from ..types.core import CommitRecord, RevealRecord, RoundId
from . import params as cr_params

# ------------------------------------------------------------------------------
# Optional Prometheus metrics (self-contained fallbacks if client is absent)
# ------------------------------------------------------------------------------

try:
    from prometheus_client import Counter  # type: ignore
except (
    Exception
):  # pragma: no cover - tiny shim for environments without prometheus_client

    class Counter:  # type: ignore
        def __init__(self, *_args, **_kwargs) -> None: ...
        def labels(self, *_args, **_kwargs) -> "Counter":  # noqa: N805
            return self

        def inc(self, *_args, **_kwargs) -> None: ...


SLASH_EVENTS = Counter(
    "randomness_slash_events_total",
    "Count of slashing events emitted by the beacon module.",
    ["kind"],
)
SLASH_AMOUNT = Counter(
    "randomness_slash_amount_total",
    "Total amount (units) suggested to slash by event kind.",
    ["kind"],
)

# ------------------------------------------------------------------------------
# Types & sink interface
# ------------------------------------------------------------------------------


class MisbehaviorKind(str, Enum):
    """Kinds of beacon misbehavior that are slashable under policy."""

    MISS = "miss"  # Did not reveal within the window
    BAD_REVEAL = "bad_reveal"  # Reveal does not match prior commitment


@dataclass(frozen=True)
class Penalty:
    """
    A structured slashing suggestion/event emitted by this module.

    Attributes
    ----------
    who:
        Participant identifier (address or key fingerprint). We treat it as an
        opaque string to avoid coupling to account/address types.
    kind:
        The misbehavior category.
    round_id:
        The round in which the infraction was observed.
    amount:
        Suggested amount to slash (integer base units). None means "unspecified"
        and is a cue for the sink to apply its own policy.
    reason:
        Human-readable explanation for logs/audit trails.
    commit:
        The original commitment (if known) for context/auditing.
    reveal:
        The reveal (if any) that triggered the failure.
    """

    who: str
    kind: MisbehaviorKind
    round_id: RoundId
    amount: Optional[int]
    reason: str
    commit: Optional[CommitRecord] = None
    reveal: Optional[RevealRecord] = None


class SlashSink(Protocol):
    """
    Consumer of slashing events.

    Implementations typically post an on-chain slash, reduce bonded stake in a
    local ledger, or enqueue an action for a governance module.
    """

    def on_slash(self, event: Penalty) -> None:
        """Handle a slashing event."""


class _NoopSink:
    def on_slash(self, _event: Penalty) -> None:  # pragma: no cover - trivial
        return


# The currently-registered sink. Defaults to a no-op.
_sink: SlashSink = _NoopSink()


def register_sink(sink: SlashSink) -> None:
    """
    Register a global slashing sink. Overwrites any previously-registered sink.

    Safe to call multiple times; intended for early initialization.
    """
    global _sink
    _sink = sink


def clear_sink() -> None:
    """Restore the default no-op sink (useful in tests)."""
    register_sink(_NoopSink())


# ------------------------------------------------------------------------------
# Policy helpers
# ------------------------------------------------------------------------------


def _enabled() -> bool:
    """
    Return True if slashing signals are enabled by policy.

    Falls back to False if the params module lacks the flag (defensive).
    """
    return bool(getattr(cr_params, "SLASH_ENABLED", False))


def _bps_for(kind: MisbehaviorKind) -> int:
    """
    Return the basis-point penalty for the given misbehavior kind.

    Missing params fall back to sane, non-explosive defaults.
    """
    if kind is MisbehaviorKind.MISS:
        return int(getattr(cr_params, "SLASH_MISS_BPS", 0))
    if kind is MisbehaviorKind.BAD_REVEAL:
        return int(getattr(cr_params, "SLASH_BAD_REVEAL_BPS", 0))
    return 0


def suggest_penalty(stake_units: int, kind: MisbehaviorKind) -> int:
    """
    Compute a suggested penalty in base units given the participant's bonded stake.

    The suggestion is stake * (bps / 10_000). Clamped to [0, stake].

    Parameters
    ----------
    stake_units:
        The participant's currently bonded stake (base units).
    kind:
        Misbehavior category.

    Returns
    -------
    int
        Suggested penalty (base units). Zero if disabled or bps is 0.
    """
    if not _enabled() or stake_units <= 0:
        return 0
    bps = _bps_for(kind)
    if bps <= 0:
        return 0
    # Use integer math; round down (conservative).
    amt = (stake_units * bps) // 10_000
    if amt < 0:
        return 0
    if amt > stake_units:
        return stake_units
    return amt


# ------------------------------------------------------------------------------
# Event emitters (to be called by the commit→reveal logic)
# ------------------------------------------------------------------------------


def record_miss(
    *,
    who: str,
    round_id: RoundId,
    commit: Optional[CommitRecord],
    stake_units: Optional[int] = None,
    reason_suffix: Optional[str] = None,
) -> Optional[Penalty]:
    """
    Emit a slashing event for a missed reveal (if enabled), returning the event.

    If slashing is disabled, returns None and performs no action.
    """
    if not _enabled():
        return None

    amt = (
        suggest_penalty(stake_units or 0, MisbehaviorKind.MISS)
        if stake_units is not None
        else None
    )
    reason = "missed reveal within window"
    if reason_suffix:
        reason = f"{reason} ({reason_suffix})"

    event = Penalty(
        who=who,
        kind=MisbehaviorKind.MISS,
        round_id=round_id,
        amount=amt,
        reason=reason,
        commit=commit,
        reveal=None,
    )
    _sink.on_slash(event)
    SLASH_EVENTS.labels(kind=MisbehaviorKind.MISS.value).inc()
    if amt:
        SLASH_AMOUNT.labels(kind=MisbehaviorKind.MISS.value).inc(amt)
    return event


def record_bad_reveal(
    *,
    who: str,
    round_id: RoundId,
    commit: CommitRecord,
    reveal: RevealRecord,
    stake_units: Optional[int] = None,
    reason_suffix: Optional[str] = None,
) -> Optional[Penalty]:
    """
    Emit a slashing event for a bad reveal (mismatch with commitment), returning the event.

    If slashing is disabled, returns None and performs no action.
    """
    if not _enabled():
        return None

    amt = (
        suggest_penalty(stake_units or 0, MisbehaviorKind.BAD_REVEAL)
        if stake_units is not None
        else None
    )
    reason = "bad reveal: does not verify against prior commitment"
    if reason_suffix:
        reason = f"{reason} ({reason_suffix})"

    event = Penalty(
        who=who,
        kind=MisbehaviorKind.BAD_REVEAL,
        round_id=round_id,
        amount=amt,
        reason=reason,
        commit=commit,
        reveal=reveal,
    )
    _sink.on_slash(event)
    SLASH_EVENTS.labels(kind=MisbehaviorKind.BAD_REVEAL.value).inc()
    if amt:
        SLASH_AMOUNT.labels(kind=MisbehaviorKind.BAD_REVEAL.value).inc(amt)
    return event


__all__ = [
    "MisbehaviorKind",
    "Penalty",
    "SlashSink",
    "register_sink",
    "clear_sink",
    "suggest_penalty",
    "record_miss",
    "record_bad_reveal",
]
