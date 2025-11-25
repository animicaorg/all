"""
execution.runtime.fees — base/tip split, burns, and gas accounting finalization.

This module turns *measured gas usage* into *coin debits/credits*:

- Refunds: apply the protocol's gas-refund cap to derive the *chargeable* gas.
- Prices: derive the effective base and priority (tip) price per gas for this tx.
- Splits: burn the base-fee portion; split the priority fee between miner (coinbase)
  and treasury by configurable basis points.
- Settlement: optionally debit payer and credit coinbase/treasury (helpers provided).

It is intentionally conservative about cross-module dependencies:
- If execution.gas.refund.finalize_refund() exists, we use it. Otherwise we cap refunds
  to 20% of gas used (EIP-3529-like).
- If execution.state.apply_balance provides debit()/credit(), we use those for settlement.
  Otherwise, callers can consume the returned FeeOutcome and settle elsewhere.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional, Tuple

BPS_DENOM = 10_000  # basis-points denominator (100% = 10_000)


# --------------------------------------------------------------------------------------
# Config & Result Types
# --------------------------------------------------------------------------------------

@dataclass(frozen=True)
class FeeConfig:
    """
    Policy knobs for fee splitting.

    treasury_tip_bps:
        Portion of *priority fee* (tips) that is credited to the treasury.
        The remainder of the priority fee goes to the miner/coinbase.
        Base fee is always burned when burn_base_fee is True.

    burn_base_fee:
        If True, base_fee_per_gas * gas_used_final is burned (payer is charged,
        no account receives it). If False, base fee is treated like a tip and
        split via treasury_tip_bps (rare; default stays True).
    """
    treasury_tip_bps: int = 0        # e.g. 1000 = 10% of priority fee to treasury
    burn_base_fee: bool = True


@dataclass
class FeeOutcome:
    """
    Computed fees for a single transaction after refund caps are applied.
    All values are integers in the chain's native currency unit.
    """
    gas_used_raw: int                 # gas consumed before refunds
    gas_refund_applied: int           # refund units actually honored (after cap)
    gas_used_final: int               # chargeable gas = gas_used_raw - gas_refund_applied

    base_fee_per_gas: int             # from block env (>=0)
    priority_fee_per_gas: int         # min(max_priority, maxFee - base); or gas_price if legacy
    effective_gas_price: int          # base + priority

    burn_amount: int                  # base burn (if enabled)
    tip_to_coinbase: int              # miner share of priority fee
    tip_to_treasury: int              # treasury share of priority fee

    total_fee: int                    # total amount debited from payer
    # Note: total_fee == burn_amount + tip_to_coinbase + tip_to_treasury when burn_base_fee=True


# --------------------------------------------------------------------------------------
# Internals: refund & pricing helpers
# --------------------------------------------------------------------------------------

def _finalize_refund(gas_used_raw: int, refund_counter: int) -> Tuple[int, int]:
    """
    Returns (refund_applied, gas_used_final).

    If execution.gas.refund.finalize_refund is available, delegate to it. Otherwise,
    cap refunds to 20% of gas_used_raw and floor at 0.
    """
    try:
        from ..gas.refund import finalize_refund  # type: ignore
        refund_applied = int(finalize_refund(gas_used_raw, refund_counter))
    except Exception:
        cap = gas_used_raw // 5  # 20%
        refund_applied = min(max(int(refund_counter), 0), cap)
    gas_used_final = max(gas_used_raw - refund_applied, 0)
    return refund_applied, gas_used_final


def _derive_prices(tx_env: Any, block_env: Any) -> Tuple[int, int, int]:
    """
    Returns (base_fee_per_gas, priority_fee_per_gas, effective_gas_price).

    Supports both 1559-style (max_fee_per_gas / max_priority_fee_per_gas)
    and legacy (gas_price) shapes on tx_env. Missing fields default to 0.
    """
    base_fee = int(getattr(block_env, "base_fee", 0) or 0)

    # 1559-style
    max_fee = getattr(tx_env, "max_fee_per_gas", None)
    max_prio = getattr(tx_env, "max_priority_fee_per_gas", None)

    if max_fee is not None and max_prio is not None:
        max_fee = int(max_fee or 0)
        max_prio = int(max_prio or 0)
        prio_cap = max(0, max_fee - base_fee)
        priority = min(max_prio, prio_cap)
        return base_fee, priority, base_fee + priority

    # Legacy gas_price
    gas_price = int(getattr(tx_env, "gas_price", 0) or 0)
    if base_fee > 0:
        # Treat legacy gas_price as total price; anything above base is priority
        priority = max(0, gas_price - base_fee)
        effective = base_fee + priority
    else:
        # No base fee regime → entire gas_price is priority
        priority = gas_price
        effective = gas_price
    return base_fee, priority, effective


# --------------------------------------------------------------------------------------
# Public API
# --------------------------------------------------------------------------------------

def finalize_accounting(
    *,
    gas_used_raw: int,
    refund_counter: int,
    tx_env: Any,
    block_env: Any,
    config: Optional[FeeConfig] = None,
) -> FeeOutcome:
    """
    Compute the fee outcome for a tx given raw gas usage and accumulated refunds.

    Args:
        gas_used_raw: measured gas consumed before refunds.
        refund_counter: refund units accumulated by the runtime (e.g., storage clears).
        tx_env: TxEnv-like object exposing either:
            - max_fee_per_gas & max_priority_fee_per_gas (1559-style), or
            - gas_price (legacy).
        block_env: BlockEnv-like object exposing base_fee (>=0) and coinbase/treasury if needed.
        config: FeeConfig with treasury split and burn behavior.

    Returns:
        FeeOutcome with all amounts computed.
    """
    cfg = config or FeeConfig()

    refund_applied, gas_used_final = _finalize_refund(gas_used_raw, refund_counter)
    base_fee, prio_fee, effective = _derive_prices(tx_env, block_env)

    # Base portion
    base_component = gas_used_final * max(base_fee, 0)
    # Priority portion
    prio_component = gas_used_final * max(prio_fee, 0)

    if cfg.burn_base_fee:
        burn_amount = base_component
        prio_for_split = prio_component
    else:
        # Treat base like a tip for splitting (rare)
        burn_amount = 0
        prio_for_split = base_component + prio_component

    tip_to_treasury = (prio_for_split * max(min(cfg.treasury_tip_bps, BPS_DENOM), 0)) // BPS_DENOM
    tip_to_coinbase = prio_for_split - tip_to_treasury

    total = burn_amount + tip_to_coinbase + tip_to_treasury

    return FeeOutcome(
        gas_used_raw=int(gas_used_raw),
        gas_refund_applied=int(refund_applied),
        gas_used_final=int(gas_used_final),
        base_fee_per_gas=int(base_fee),
        priority_fee_per_gas=int(prio_fee),
        effective_gas_price=int(effective),
        burn_amount=int(burn_amount),
        tip_to_coinbase=int(tip_to_coinbase),
        tip_to_treasury=int(tip_to_treasury),
        total_fee=int(total),
    )


def settle_fees(
    state: Any,
    payer: bytes,
    *,
    coinbase: Optional[bytes],
    treasury: Optional[bytes],
    outcome: FeeOutcome,
) -> None:
    """
    Apply balance changes implied by the computed FeeOutcome.

    Debits the payer by total_fee, then credits coinbase & treasury with their
    respective tip shares. The burn portion is intentionally not credited.

    This function requires execution.state.apply_balance.{debit,credit}. If those
    helpers are not available, this function raises ImportError and callers should
    perform settlement with their own state adapter.
    """
    # Lazy import to avoid hard dependency at import time
    from ..state.apply_balance import debit, credit  # type: ignore

    # 1) Debit payer for the entire fee (burn + tips)
    if outcome.total_fee:
        debit(state, payer, outcome.total_fee)

    # 2) Credit coinbase (miner) with its tip share
    if coinbase and outcome.tip_to_coinbase:
        credit(state, coinbase, outcome.tip_to_coinbase)

    # 3) Credit treasury with its cut (if any/configured)
    if treasury and outcome.tip_to_treasury:
        credit(state, treasury, outcome.tip_to_treasury)
    # Burn is implicit: no credit performed.


__all__ = [
    "FeeConfig",
    "FeeOutcome",
    "finalize_accounting",
    "settle_fees",
]
