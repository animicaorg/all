from __future__ import annotations
"""
Batch settlements for the AI Compute Fund (AICF).

This module aggregates many per-job `Payout` entries into a compact list of
transfers to be executed from the AICF treasury account. It also enforces the
per-epoch Γ_fund budget (see `aicf.economics.epochs`) so that settlements never
overspend the fund.

Typical flow
------------
1) Collect `Payout` records for a block range or accounting epoch.
2) Build a plan by aggregating amounts by payee (provider / miner).
3) Enforce the epoch budget; split into accepted and rejected (deferrable).
4) Emit `TransferInstruction`s to the treasury for on-chain execution.

Design notes
------------
- Deterministic: aggregation order is stable (sorted by kind + address).
- Dust filtering: optional `min_unit` threshold to drop tiny transfers.
- Epoch budget: integrates with `EpochAccounting.try_reserve()` per transfer.

Inputs
------
- Payout: a record produced from verified proof claims (see aicf.aitypes.payout).
  We expect at least:
    - provider_id: str
    - amount_provider: int
    - miner_address: Optional[str]
    - amount_miner: int
    - amount_treasury: int   # recorded for accounting; no outbound transfer
  If field names differ slightly, adapt the accessor helpers below.

- provider_address_book: Mapping[ProviderId -> payout_address]
  The payout address for a provider (could be an L1/L2 account or bech32m).

Outputs
-------
- SettlementPlan: contains accepted and rejected transfers and summary stats.
- TransferInstruction: a simple "from treasury → payee" transfer order.

This module does not perform any IO or database mutations.
"""


from dataclasses import dataclass, replace
from typing import Dict,Iterable,List,Mapping,MutableMapping,Optional,Tuple

from aicf.economics.epochs import EpochAccounting, try_reserve


# ------------------------------- Data types --------------------------------- #

PayeeKind = Literal["provider", "miner"]


@dataclass(frozen=True)
class TransferInstruction:
    """
    An outbound transfer from the AICF treasury account to a payee.
    """
    source_account: str
    to_kind: PayeeKind
    to_address: str
    amount: int
    memo: Optional[str] = None


@dataclass(frozen=True)
class SettlementPlan:
    """
    A concrete settlement plan for an epoch window.
    """
    epoch_idx: int
    accepted: List[TransferInstruction]
    rejected: List[TransferInstruction]
    treasury_accrual: int  # sum of `amount_treasury` from payouts (no outbound)
    total_requested: int   # sum of requested outbound amounts (provider + miner)
    total_accepted: int    # sum(amount) over `accepted`
    total_rejected: int    # sum(amount) over `rejected`


# ----------------------------- Accessor helpers ----------------------------- #

def _payout_get(payout: object, name: str, default: object = 0) -> object:
    """
    Safe getattr with a default for flexible Payout structures.
    """
    return getattr(payout, name, default)  # type: ignore[attr-defined]


def _provider_id(payout: object) -> str:
    v = _payout_get(payout, "provider_id", "")
    if not isinstance(v, str):
        raise TypeError("Payout.provider_id must be str")
    return v


def _amount_provider(payout: object) -> int:
    v = _payout_get(payout, "amount_provider", 0)
    if not isinstance(v, int):
        raise TypeError("Payout.amount_provider must be int")
    return v


def _miner_address(payout: object) -> Optional[str]:
    # allow either 'miner_address' or 'miner'
    v = _payout_get(payout, "miner_address", None)
    if v is None:
        v = _payout_get(payout, "miner", None)
    if v is None:
        return None
    if not isinstance(v, str):
        raise TypeError("Payout.miner_address must be str if present")
    return v


def _amount_miner(payout: object) -> int:
    v = _payout_get(payout, "amount_miner", 0)
    if not isinstance(v, int):
        raise TypeError("Payout.amount_miner must be int")
    return v


def _amount_treasury(payout: object) -> int:
    v = _payout_get(payout, "amount_treasury", 0)
    if not isinstance(v, int):
        raise TypeError("Payout.amount_treasury must be int")
    return v


# ----------------------------- Core aggregation ----------------------------- #

def aggregate_by_payee(
    payouts: Iterable[object],
    provider_address_book: Mapping[str, str],
    *,
    include_miners: bool = True,
    min_unit: int = 1,
) -> Tuple[Dict[str, int], Dict[str, int], int]:
    """
    Aggregate payouts into consolidated amounts per payee address.

    Returns:
        (provider_totals_by_address, miner_totals_by_address, treasury_accrual)

    - `min_unit` drops any individual line item smaller than the threshold.
    - Providers with missing payout addresses are silently skipped (amounts
      will be implicitly rejected later if desired).
    """
    providers: Dict[str, int] = {}
    miners: Dict[str, int] = {}
    treasury_sum = 0

    for p in payouts:
        pid = _provider_id(p)
        amt_p = _amount_provider(p)
        amt_m = _amount_miner(p)
        amt_t = _amount_treasury(p)
        treasury_sum += max(amt_t, 0)

        # Provider line
        if amt_p >= min_unit:
            to_addr = provider_address_book.get(pid)
            if to_addr:
                providers[to_addr] = providers.get(to_addr, 0) + amt_p

        # Miner line
        if include_miners and amt_m >= min_unit:
            m_addr = _miner_address(p)
            if m_addr:
                miners[m_addr] = miners.get(m_addr, 0) + amt_m

    return providers, miners, treasury_sum


def _mk_transfer_list(
    source_account: str,
    epoch_idx: int,
    providers: Mapping[str, int],
    miners: Mapping[str, int],
    memo_prefix: str = "AICF epoch",
) -> List[TransferInstruction]:
    """
    Build a stable, deterministic list of transfer instructions.
    Ordering: providers (by address asc) then miners (by address asc).
    """
    transfers: List[TransferInstruction] = []

    for addr in sorted(providers.keys()):
        amt = providers[addr]
        if amt > 0:
            transfers.append(
                TransferInstruction(
                    source_account=source_account,
                    to_kind="provider",
                    to_address=addr,
                    amount=amt,
                    memo=f"{memo_prefix} {epoch_idx} / provider",
                )
            )

    for addr in sorted(miners.keys()):
        amt = miners[addr]
        if amt > 0:
            transfers.append(
                TransferInstruction(
                    source_account=source_account,
                    to_kind="miner",
                    to_address=addr,
                    amount=amt,
                    memo=f"{memo_prefix} {epoch_idx} / miner",
                )
            )

    return transfers


# ------------------------------ Budget enforcement -------------------------- #

def enforce_epoch_budget(
    epoch_state: EpochAccounting,
    transfers: Iterable[TransferInstruction],
) -> Tuple[EpochAccounting, List[TransferInstruction], List[TransferInstruction]]:
    """
    Apply the epoch Γ_fund cap to a list of transfers.

    Returns:
        (new_epoch_state, accepted_transfers, rejected_transfers)

    Deterministic rule: iterate in the given order, accept if there is room,
    otherwise reject. Typically, callers pass a pre-sorted list.
    """
    accepted: List[TransferInstruction] = []
    rejected: List[TransferInstruction] = []
    cur = epoch_state

    for t in transfers:
        ok, next_state = try_reserve(cur, t.amount)
        if ok:
            accepted.append(t)
            cur = next_state
        else:
            rejected.append(t)

    return cur, accepted, rejected


# --------------------------------- Planning --------------------------------- #

def build_settlement_plan(
    epoch_idx: int,
    source_account: str,
    payouts: Iterable[object],
    provider_address_book: Mapping[str, str],
    epoch_state: EpochAccounting,
    *,
    include_miners: bool = True,
    min_unit: int = 1,
    memo_prefix: str = "AICF epoch",
) -> Tuple[SettlementPlan, EpochAccounting]:
    """
    Build a settlement plan from raw payouts and enforce the epoch budget.

    Args:
        epoch_idx: The epoch index to annotate in transfer memos.
        source_account: Treasury account address (fund source).
        payouts: Iterable of Payout-like objects.
        provider_address_book: ProviderId → payout address.
        epoch_state: Current `EpochAccounting` state for this epoch.
        include_miners: Whether to pay miners' share in this batch.
        min_unit: Drop line items smaller than this (anti-dust).
        memo_prefix: Human-friendly memo prefix.

    Returns:
        (plan, new_epoch_state)
    """
    prov_map, miner_map, treasury_accrual = aggregate_by_payee(
        payouts, provider_address_book, include_miners=include_miners, min_unit=min_unit
    )

    transfers = _mk_transfer_list(
        source_account=source_account,
        epoch_idx=epoch_idx,
        providers=prov_map,
        miners=miner_map,
        memo_prefix=memo_prefix,
    )

    total_requested = sum(t.amount for t in transfers)

    new_state, accepted, rejected = enforce_epoch_budget(epoch_state, transfers)
    total_accepted = sum(t.amount for t in accepted)
    total_rejected = sum(t.amount for t in rejected)

    plan = SettlementPlan(
        epoch_idx=epoch_idx,
        accepted=accepted,
        rejected=rejected,
        treasury_accrual=treasury_accrual,
        total_requested=total_requested,
        total_accepted=total_accepted,
        total_rejected=total_rejected,
    )
    return plan, new_state


# ------------------------------ Pretty utilities ---------------------------- #

def summarize_plan(plan: SettlementPlan) -> str:
    """
    Produce a one-line summary suitable for logs.
    """
    parts = [
        f"epoch={plan.epoch_idx}",
        f"accepted={len(plan.accepted)}",
        f"rejected={len(plan.rejected)}",
        f"sum_req={plan.total_requested}",
        f"sum_acc={plan.total_accepted}",
        f"sum_rej={plan.total_rejected}",
        f"treasury_accrual={plan.treasury_accrual}",
    ]
    return "SettlementPlan{" + " ".join(parts) + "}"


__all__ = [
    "PayeeKind",
    "TransferInstruction",
    "SettlementPlan",
    "aggregate_by_payee",
    "enforce_epoch_budget",
    "build_settlement_plan",
    "summarize_plan",
]
