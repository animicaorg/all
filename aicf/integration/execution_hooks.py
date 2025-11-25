from __future__ import annotations
"""
aicf.integration.execution_hooks
--------------------------------

Block-apply integration hooks to materialize AICF payouts into the execution
state. Call these from the block executor *after* proofs have been validated
and payout entries computed, but *before* finalizing the block state.

The hooks are intentionally lightweight and duck-typed so they can be wired
into different state backends during devnet or production.

Typical usage (inside apply_block):

    from aicf.integration.execution_hooks import record_payouts_in_state
    from execution.runtime import system as sys_accts

    # payouts: Iterable[Payout] built by aicf.economics.payouts.build_from_claims(...)
    record_payouts_in_state(
        state=state_view_or_tx,                 # execution/state journal-like object
        payouts=payouts,
        coinbase=block.coinbase,                # miner address for the block
        treasury=getattr(sys_accts, "TREASURY_ADDRESS", "anim1treasury..."),
        emit_event=event_sink.append if event_sink else None,
    )

This will debit the treasury and credit the provider and miner according to the
`RewardSplit` on each `Payout`. The treasury's own share is a no-op (it remains
in the treasury account).

Determinism & safety:
  * Deterministic arithmetic with saturating / explicit underflow checks.
  * Idempotence is left to the caller: invoke exactly once per block application.
  * Execution-layer journaling/rollback handles reorg safety.

"""


from dataclasses import asdict
from typing import Any, Callable, Iterable, Mapping, Optional, Protocol, runtime_checkable

# --------- Types from AICF (only for type hints; runtime optional) -----------

try:
    from aicf.aitypes.payout import Payout, RewardSplit  # type: ignore
except Exception:  # pragma: no cover
    # Minimal shims for static typing without importing full module at runtime.
    class RewardSplit:  # type: ignore
        provider: int
        miner: int
        treasury: int

        def __init__(self, provider: int, miner: int, treasury: int) -> None:
            self.provider = provider
            self.miner = miner
            self.treasury = treasury

    class Payout:  # type: ignore
        task_id: str
        kind: str
        provider_address: str | bytes
        miner_address: Optional[str | bytes]
        split: RewardSplit

        def __init__(
            self,
            task_id: str,
            kind: str,
            provider_address: str | bytes,
            miner_address: Optional[str | bytes],
            split: RewardSplit,
        ) -> None:
            self.task_id = task_id
            self.kind = kind
            self.provider_address = provider_address
            self.miner_address = miner_address
            self.split = split


# ----- Optional imports from execution layer (fall back if unavailable) ------

# apply-balance helpers (preferred)
try:  # pragma: no cover
    from execution.state.apply_balance import (
        credit_balance as _exec_credit_balance,  # type: ignore
        debit_balance as _exec_debit_balance,    # type: ignore
    )
except Exception:  # pragma: no cover
    _exec_credit_balance = None
    _exec_debit_balance = None


# --------------------------- State duck-typing --------------------------------

@runtime_checkable
class _StateLike(Protocol):
    """
    Minimal state protocol we rely on. Your execution/state implementation can
    expose richer APIs; we only need balance get/set or credit/debit helpers.
    """

    # Preferred granular ops
    def get_balance(self, address: str | bytes) -> int: ...
    def set_balance(self, address: str | bytes, value: int) -> None: ...

    # Optional helpers (if present, we will use them)
    def credit_balance(self, address: str | bytes, amount: int) -> None: ...  # noqa: D401,E701
    def debit_balance(self, address: str | bytes, amount: int) -> None: ...   # noqa: D401,E701


# ------------------------------- Utilities ------------------------------------

def _to_int(x: int) -> int:
    if not isinstance(x, int):
        raise TypeError(f"amount must be int, got {type(x)}")
    if x < 0:
        raise ValueError("amount must be non-negative")
    return x


def _credit(state: _StateLike, addr: str | bytes, amount: int) -> None:
    amount = _to_int(amount)
    if amount == 0:
        return
    # Prefer execution-layer helpers if available
    if hasattr(state, "credit_balance"):
        state.credit_balance(addr, amount)  # type: ignore[attr-defined]
        return
    if _exec_credit_balance is not None:
        _exec_credit_balance(state, addr, amount)  # type: ignore[misc]
        return
    # Fallback: get/set
    new_val = state.get_balance(addr) + amount
    if new_val < 0:  # pragma: no cover - defensive, should never happen
        raise OverflowError("balance overflow on credit")
    state.set_balance(addr, new_val)


def _debit(state: _StateLike, addr: str | bytes, amount: int) -> None:
    amount = _to_int(amount)
    if amount == 0:
        return
    if hasattr(state, "debit_balance"):
        state.debit_balance(addr, amount)  # type: ignore[attr-defined]
        return
    if _exec_debit_balance is not None:
        _exec_debit_balance(state, addr, amount)  # type: ignore[misc]
        return
    # Fallback: get/set with underflow check
    cur = state.get_balance(addr)
    if cur < amount:
        raise ValueError("treasury balance underflow while applying payouts")
    state.set_balance(addr, cur - amount)


def _event_map(kind: str, name: str, data: Mapping[str, Any]) -> Mapping[str, Any]:
    """
    Lightweight event mapping. The execution layer can translate this into
    its native event/log format if desired.
    """
    return {
        "module": "aicf",
        "kind": kind,
        "name": name,
        "data": dict(data),
    }


# ------------------------------ Main Hook -------------------------------------

def record_payouts_in_state(
    *,
    state: _StateLike,
    payouts: Iterable[Payout],
    coinbase: str | bytes,
    treasury: str | bytes,
    emit_event: Optional[Callable[[Mapping[str, Any]], None]] = None,
) -> None:
    """
    Apply AICF payouts to execution state:

      - Debit `treasury` by (provider_share + miner_share) for each payout.
      - Credit provider address with provider_share.
      - Credit coinbase (or payout.miner_address if provided) with miner_share.
      - (Treasury share remains in the treasury; no transfer needed.)

    Emission of lightweight audit events is optional and pluggable via
    `emit_event`. Events emitted (once per payout):
      - AICF.PayoutApplied {task_id, kind, provider, miner, provider_amount, miner_amount}

    Raises:
      ValueError on treasury underflow if insufficient funds.
    """
    for p in payouts:
        # Normalize split amounts
        prov_amt = _to_int(int(p.split.provider))
        miner_amt = _to_int(int(p.split.miner))
        # No transfer for treasury share — it's already there

        total_debit = prov_amt + miner_amt
        if total_debit > 0:
            _debit(state, treasury, total_debit)

        # Credit provider
        if prov_amt > 0:
            _credit(state, p.provider_address, prov_amt)

        # Credit miner (prefer payout-provided miner address, else block coinbase)
        miner_addr = p.miner_address if getattr(p, "miner_address", None) else coinbase
        if miner_amt > 0:
            _credit(state, miner_addr, miner_amt)

        if emit_event:
            try:
                emit_event(
                    _event_map(
                        "payout",
                        "AICF.PayoutApplied",
                        {
                            "task_id": p.task_id,
                            "kind": p.kind,
                            "provider": _addr_str(p.provider_address),
                            "miner": _addr_str(miner_addr),
                            "provider_amount": prov_amt,
                            "miner_amount": miner_amt,
                            "treasury_retained": _to_int(int(p.split.treasury)),
                        },
                    )
                )
            except Exception:
                # Events are best-effort; state changes already applied
                pass


# ------------------------------ Pretty helpers --------------------------------

def _addr_str(a: str | bytes) -> str:
    if isinstance(a, bytes):
        return "0x" + a.hex()
    return a


__all__ = ["record_payouts_in_state"]
