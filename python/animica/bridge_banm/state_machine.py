from __future__ import annotations

from collections.abc import Iterable

from .enums import BridgeStatus


TERMINAL_STATUSES = {
    BridgeStatus.COMPLETED,
    BridgeStatus.EXPIRED,
    BridgeStatus.REJECTED,
    BridgeStatus.FAILED,
    BridgeStatus.CANCELLED,
}

ALLOWED_TRANSITIONS: dict[BridgeStatus, set[BridgeStatus]] = {
    BridgeStatus.CREATED: {
        BridgeStatus.AWAITING_DEPOSIT,
        BridgeStatus.EXPIRED,
        BridgeStatus.REJECTED,
        BridgeStatus.CANCELLED,
    },
    BridgeStatus.AWAITING_DEPOSIT: {
        BridgeStatus.DEPOSIT_SEEN,
        BridgeStatus.EXPIRED,
        BridgeStatus.MANUAL_REVIEW,
        BridgeStatus.CANCELLED,
    },
    BridgeStatus.DEPOSIT_SEEN: {
        BridgeStatus.CONFIRMING,
        BridgeStatus.CONFIRMED,
        BridgeStatus.MANUAL_REVIEW,
        BridgeStatus.FAILED,
    },
    BridgeStatus.CONFIRMING: {
        BridgeStatus.CONFIRMED,
        BridgeStatus.MANUAL_REVIEW,
        BridgeStatus.FAILED,
    },
    BridgeStatus.CONFIRMED: {
        BridgeStatus.READY_TO_SETTLE,
        BridgeStatus.MANUAL_REVIEW,
    },
    BridgeStatus.READY_TO_SETTLE: {
        BridgeStatus.SETTLEMENT_SUBMITTED,
        BridgeStatus.MANUAL_REVIEW,
        BridgeStatus.FAILED,
    },
    BridgeStatus.SETTLEMENT_SUBMITTED: {
        BridgeStatus.SETTLEMENT_CONFIRMED,
        BridgeStatus.MANUAL_REVIEW,
        BridgeStatus.FAILED,
    },
    BridgeStatus.SETTLEMENT_CONFIRMED: {
        BridgeStatus.COMPLETED,
        BridgeStatus.MANUAL_REVIEW,
        BridgeStatus.FAILED,
    },
    BridgeStatus.MANUAL_REVIEW: {
        BridgeStatus.CONFIRMING,
        BridgeStatus.READY_TO_SETTLE,
        BridgeStatus.REJECTED,
        BridgeStatus.FAILED,
        BridgeStatus.CANCELLED,
    },
    BridgeStatus.COMPLETED: set(),
    BridgeStatus.EXPIRED: set(),
    BridgeStatus.REJECTED: set(),
    BridgeStatus.FAILED: set(),
    BridgeStatus.CANCELLED: set(),
}


def can_transition(current: BridgeStatus, nxt: BridgeStatus) -> bool:
    if current == nxt:
        return True
    return nxt in ALLOWED_TRANSITIONS.get(current, set())


def assert_transition(current: BridgeStatus, nxt: BridgeStatus) -> None:
    if not can_transition(current, nxt):
        raise ValueError(f"illegal state transition: {current.value} -> {nxt.value}")


def assert_in(current: BridgeStatus, allowed: Iterable[BridgeStatus]) -> None:
    allowed_set = set(allowed)
    if current not in allowed_set:
        joined = ", ".join(state.value for state in sorted(allowed_set, key=lambda s: s.value))
        raise ValueError(f"status {current.value} not in allowed set: {joined}")
