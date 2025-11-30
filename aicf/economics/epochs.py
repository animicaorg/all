from __future__ import annotations

"""
Epoch accounting for the AI Compute Fund (AICF).

We track per-epoch budgets and enforce a hard cap Γ_fund on payouts within each
epoch. Unused budget can (optionally) roll over at a configurable fraction.

- Epochs are defined over block heights: fixed-length windows starting at
  `start_height`, length `length` (in blocks). Height `h` is in epoch:
      e = floor((h - start_height) / length),  for h >= start_height
- Each epoch has a total budget `budget_total` and a running `budget_spent`.
- Calls to `try_reserve()` atomically check + reserve against the remaining cap.
- At epoch rollover, the next epoch's budget is:
      base_budget + floor(unused_prev * rollover_rate)

This module is deterministic and side-effect free; persistence is the caller's
responsibility (e.g., store one `EpochAccounting` record per epoch in KV/SQL).

Terminology / symbols
- Γ_fund (gamma_fund): the per-epoch fund cap (a budget, in chain units)
- base_budget: the per-epoch baseline contribution to Γ_fund
- rollover_rate ∈ [0,1]: fraction of unused that carries into the next epoch
"""


from dataclasses import asdict, dataclass, replace
from math import floor
from typing import (Dict, Iterable, List, Mapping, MutableMapping, Optional,
                    Tuple)

# ------------------------------ Params & State ------------------------------ #


@dataclass(frozen=True)
class EpochParams:
    """
    Static epoch configuration.

    Attributes:
        start_height: first height at which epoch accounting begins.
        length: number of blocks per epoch (must be > 0).
        base_budget: baseline Γ_fund per epoch (integer token units).
        rollover_rate: fraction of unused budget carried into the next epoch.
                       Must be between 0.0 and 1.0 inclusive.
    """

    start_height: int = 0
    length: int = 720
    base_budget: int = 0
    rollover_rate: float = 0.0

    def __post_init__(self) -> None:
        if self.length <= 0:
            raise ValueError("EpochParams.length must be > 0")
        if self.start_height < 0:
            raise ValueError("EpochParams.start_height must be >= 0")
        if self.base_budget < 0:
            raise ValueError("EpochParams.base_budget must be >= 0")
        if not (0.0 <= self.rollover_rate <= 1.0):
            raise ValueError("EpochParams.rollover_rate must be within [0.0, 1.0]")


@dataclass(frozen=True)
class EpochIndex:
    """
    0-indexed epoch identifier + its [start, end) height bounds.
    """

    idx: int
    start_height: int
    end_height_exclusive: int


@dataclass(frozen=True)
class EpochAccounting:
    """
    Mutable logical state for one epoch's accounting (store this per-epoch).

    Attributes:
        epoch: index and bounds of the epoch.
        budget_total: Γ_fund for the epoch (post-rollover).
        budget_spent: amount reserved/spent so far in this epoch.
        payouts_count: number of payouts recorded (for stats/audits).
    """

    epoch: EpochIndex
    budget_total: int
    budget_spent: int = 0
    payouts_count: int = 0

    @property
    def remaining(self) -> int:
        r = self.budget_total - self.budget_spent
        return 0 if r < 0 else r

    def to_dict(self) -> Dict[str, int]:
        d = asdict(self)
        # flatten nested epoch for convenience
        d["epoch_idx"] = self.epoch.idx
        d["epoch_start"] = self.epoch.start_height
        d["epoch_end_excl"] = self.epoch.end_height_exclusive
        # preserve originals too
        return d  # type: ignore[return-value]


# ---------------------------------- Helpers --------------------------------- #


def epoch_index_for_height(h: int, params: EpochParams) -> EpochIndex:
    """
    Compute the 0-indexed epoch index and its bounds for a given block height.
    Heights before params.start_height map to idx = -1 (sentinel).
    """
    if h < 0:
        raise ValueError("height must be >= 0")
    if h < params.start_height:
        return EpochIndex(
            idx=-1, start_height=0, end_height_exclusive=params.start_height
        )

    offset = h - params.start_height
    idx = offset // params.length
    start = params.start_height + idx * params.length
    end_excl = start + params.length
    return EpochIndex(idx=idx, start_height=start, end_height_exclusive=end_excl)


def next_epoch_index(cur: EpochIndex, params: EpochParams) -> EpochIndex:
    idx = max(cur.idx, -1) + 1
    start = params.start_height + idx * params.length
    end_excl = start + params.length
    return EpochIndex(idx=idx, start_height=start, end_height_exclusive=end_excl)


def compute_next_budget(prev: Optional[EpochAccounting], params: EpochParams) -> int:
    """
    Determine the next epoch's Γ_fund budget from params + any rollover.
    """
    carry = 0
    if prev is not None:
        unused = max(prev.budget_total - prev.budget_spent, 0)
        carry = floor(unused * params.rollover_rate)
    return params.base_budget + carry


def start_epoch_for_height(
    h: int, params: EpochParams, prev: Optional[EpochAccounting]
) -> EpochAccounting:
    """
    Start (or re-compute) the accounting record for the epoch containing height `h`.
    If `prev` refers to the immediately preceding epoch, rollover is applied.
    """
    eidx = epoch_index_for_height(h, params)
    if eidx.idx < 0:
        # We're before accounting starts; return a zero-budget placeholder
        return EpochAccounting(
            epoch=eidx, budget_total=0, budget_spent=0, payouts_count=0
        )

    expected_prev_idx = eidx.idx - 1
    prev_for_roll = (
        prev if (prev is not None and prev.epoch.idx == expected_prev_idx) else None
    )
    budget = compute_next_budget(prev_for_roll, params)
    return EpochAccounting(
        epoch=eidx, budget_total=budget, budget_spent=0, payouts_count=0
    )


# ---------------------------- Reservation Interface ------------------------- #


def try_reserve(state: EpochAccounting, amount: int) -> Tuple[bool, EpochAccounting]:
    """
    Attempt to reserve `amount` from the remaining Γ_fund in this epoch.
    Returns (ok, new_state). No mutation in-place (functional style).
    """
    if amount < 0:
        raise ValueError("amount must be >= 0")
    if amount == 0:
        return True, replace(state, payouts_count=state.payouts_count + 1)

    if amount <= state.remaining:
        return True, replace(
            state,
            budget_spent=state.budget_spent + amount,
            payouts_count=state.payouts_count + 1,
        )
    return False, state


def apply_refund(state: EpochAccounting, amount: int) -> EpochAccounting:
    """
    Apply a refund (negative spend) within the same epoch (e.g., reversed claim).
    """
    if amount < 0:
        raise ValueError("refund amount must be >= 0")
    new_spent = state.budget_spent - amount
    if new_spent < 0:
        new_spent = 0
    return replace(state, budget_spent=new_spent)


def cap_batch_spend(
    state: EpochAccounting, amounts: Iterable[int]
) -> Tuple[EpochAccounting, List[int], List[int]]:
    """
    Consume a batch of spend amounts in the given order until the epoch budget is exhausted.

    Returns:
        (new_state, accepted_amounts, rejected_amounts)
    """
    accepted: List[int] = []
    rejected: List[int] = []
    cur = state
    for amt in amounts:
        ok, next_state = try_reserve(cur, amt)
        if ok:
            accepted.append(amt)
            cur = next_state
        else:
            rejected.append(amt)
    return cur, accepted, rejected


# ----------------------------- Serialization Utils -------------------------- #


def encode_state(state: EpochAccounting) -> Mapping[str, int]:
    """
    Stable dict encoding (for JSON/msgpack/CBOR persistence).
    """
    return {
        "epoch_idx": state.epoch.idx,
        "epoch_start": state.epoch.start_height,
        "epoch_end_excl": state.epoch.end_height_exclusive,
        "budget_total": state.budget_total,
        "budget_spent": state.budget_spent,
        "payouts_count": state.payouts_count,
    }


def decode_state(d: Mapping[str, int]) -> EpochAccounting:
    """
    Inverse of `encode_state`.
    """
    eidx = EpochIndex(
        idx=int(d["epoch_idx"]),
        start_height=int(d["epoch_start"]),
        end_height_exclusive=int(d["epoch_end_excl"]),
    )
    return EpochAccounting(
        epoch=eidx,
        budget_total=int(d["budget_total"]),
        budget_spent=int(d.get("budget_spent", 0)),
        payouts_count=int(d.get("payouts_count", 0)),
    )


__all__ = [
    "EpochParams",
    "EpochIndex",
    "EpochAccounting",
    "epoch_index_for_height",
    "next_epoch_index",
    "compute_next_budget",
    "start_epoch_for_height",
    "try_reserve",
    "apply_refund",
    "cap_batch_spend",
    "encode_state",
    "decode_state",
]
