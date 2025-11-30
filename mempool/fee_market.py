"""
mempool.fee_market
==================

Dynamic fee-floor & suggestion logic used by the mempool:

• Maintains an EMA-based floor price from recent blocks (observed min-accepted fees)
• Reacts to congestion with a surge multiplier derived from pending gas pressure
• Provides base/tip split helpers compatible with legacy & 1559-style txs
• Supplies a single admission policy decision (accept/reject + reason)

This module is *pure* (no IO). Persist FeeMarketState externally if desired.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Tuple

# ---------- Units ----------
WEI = 1
GWEI = 10**9


# ---------- Config & State ----------


@dataclass(frozen=True)
class FeeMarketConfig:
    # Target utilization (fraction of gas limit). Common value: 0.5
    target_utilization: float = 0.50

    # EMA smoothing factors (0..1], larger = more responsive
    ema_alpha_price: float = 0.20
    ema_alpha_util: float = 0.20

    # Per-block change clamp for the price floor (e.g., 12.5% ~ EIP-1559 spirit)
    change_limit: float = 0.125

    # Absolute clamps
    min_base_fee: int = 1 * GWEI
    max_base_fee: int = 1_000 * GWEI

    # Tip floor (to avoid 0-tip griefing)
    min_tip: int = 1 * GWEI

    # Surge controls: trigger after ~N blocks worth of pending gas at target capacity
    surge_pending_blocks: float = 3.0
    # Surge multiplier slope (each extra "block" of pending gas increases floor)
    surge_beta: float = 0.25
    # Hard cap on surge multiplier
    surge_cap: float = 4.0


@dataclass
class FeeMarketState:
    """
    Keep a rolling estimate of the base fee floor & utilization pressure.
    """

    height: int = 0
    ema_floor: int = 1 * GWEI
    ema_util: float = 0.50
    fullness_streak: int = 0  # consecutive > target blocks

    def copy(self) -> "FeeMarketState":
        return FeeMarketState(
            height=self.height,
            ema_floor=self.ema_floor,
            ema_util=self.ema_util,
            fullness_streak=self.fullness_streak,
        )


# ---------- Mempool pressure snapshot ----------


@dataclass(frozen=True)
class MempoolPressure:
    """
    Pending gas and counts at the moment of admission/suggestion.
    """

    pending_txs: int
    pending_gas: int  # total gas of all pending txs
    # Gas limit of *current parent* (used to normalize pending gas)
    block_gas_limit: int


# ---------- Helpers ----------


def _ema(prev: float, obs: float, alpha: float) -> float:
    return (1.0 - alpha) * prev + alpha * obs


def _clamp_int(val: int, lo: int, hi: int) -> int:
    return max(lo, min(hi, val))


def _clamp_change(prev: int, nxt: int, limit_frac: float) -> int:
    """
    Clamp the absolute per-step change to ±limit_frac of prev.
    """
    if prev <= 0:
        return nxt
    max_up = int(prev * (1.0 + limit_frac))
    max_down = int(prev * (1.0 - limit_frac))
    return max(min(nxt, max_up), max_down)


# ---------- Core update ----------


def update_on_block(
    state: FeeMarketState,
    *,
    height: int,
    gas_used: int,
    gas_limit: int,
    cfg: FeeMarketConfig,
    # Optional: If the block contains txs, pass min/median gas price actually included.
    observed_min_accepted_fee: Optional[int] = None,
    observed_p50_fee: Optional[int] = None,
) -> FeeMarketState:
    """
    Update EMA floor & utilization based on a new canonical block.
    """
    s = state.copy()
    s.height = height

    target_gas = int(cfg.target_utilization * gas_limit)
    util = 0.0 if gas_limit == 0 else gas_used / float(gas_limit)
    s.ema_util = _ema(s.ema_util, util, cfg.ema_alpha_util)

    # Track streak of full-ish blocks (over target utilization)
    if gas_used > target_gas:
        s.fullness_streak += 1
    else:
        s.fullness_streak = 0

    # Price observation for EMA:
    # Prefer median if supplied; otherwise use min-accepted; fallback to utilization-derived
    if observed_p50_fee is not None:
        obs_price = int(observed_p50_fee)
    elif observed_min_accepted_fee is not None:
        obs_price = int(observed_min_accepted_fee)
    else:
        # Utilization-derived synthetic signal:
        # scale previous floor by pressure relative to target
        pressure = (util - cfg.target_utilization) / max(1e-9, cfg.target_utilization)
        # Example: at target → pressure=0 (no change). 2x target (i.e., full @ 0.5 target) → +100%
        obs_price = int(round(state.ema_floor * (1.0 + max(-0.9, pressure))))

    # Smooth & clamp
    raw_next = int(round(_ema(state.ema_floor, obs_price, cfg.ema_alpha_price)))
    clamped = _clamp_change(state.ema_floor, raw_next, cfg.change_limit)
    s.ema_floor = _clamp_int(clamped, cfg.min_base_fee, cfg.max_base_fee)
    return s


# ---------- Surge multiplier ----------


def surge_multiplier(pressure: MempoolPressure, cfg: FeeMarketConfig) -> float:
    """
    Convert pending gas into a multiplicative surge factor.

    pending_blocks = pending_gas / (target_util * block_gas_limit)

    multiplier = 1 + beta * max(0, pending_blocks - surge_pending_blocks)
    """
    denom = max(1, int(cfg.target_utilization * pressure.block_gas_limit))
    pending_blocks = pressure.pending_gas / float(denom)
    over = max(0.0, pending_blocks - cfg.surge_pending_blocks)
    mult = 1.0 + cfg.surge_beta * over
    return float(min(cfg.surge_cap, max(1.0, mult)))


# ---------- Floor computation & suggestions ----------


@dataclass(frozen=True)
class FeeSuggestion:
    base_fee: int  # suggested base fee (floor before surge)
    surge_multiplier: float
    floor_with_surge: int  # admission floor after surge
    min_tip: int  # tip floor (cfg.min_tip)
    recommended_tip: int  # heuristic: higher of tip floor or ~10% of base
    # Convenience for UX
    min_total_price: int  # floor_with_surge + min_tip
    suggested_legacy_gas_price: int  # for legacy txs (gasPrice)


def suggest_fees(
    state: FeeMarketState,
    pressure: MempoolPressure,
    cfg: FeeMarketConfig,
) -> FeeSuggestion:
    base = int(state.ema_floor)
    mult = surge_multiplier(pressure, cfg)
    surged = int(
        _clamp_int(int(round(base * mult)), cfg.min_base_fee, cfg.max_base_fee)
    )
    tip_floor = int(cfg.min_tip)
    tip_suggest = max(tip_floor, max(1, base // 10))  # ~10% of base
    return FeeSuggestion(
        base_fee=base,
        surge_multiplier=mult,
        floor_with_surge=surged,
        min_tip=tip_floor,
        recommended_tip=tip_suggest,
        min_total_price=surged + tip_floor,
        suggested_legacy_gas_price=surged + tip_suggest,
    )


# ---------- Base/Tip split & admission ----------


def effective_gas_price(tx, *, base_fee: int) -> Tuple[int, int, int]:
    """
    Compute (effective_price, base_component, tip_component) the sender will pay.

    Legacy:
        gas_price present → effective = gas_price
        base_paid = min(gas_price, base_fee)
        tip_paid  = max(0, gas_price - base_fee)

    1559-style:
        effective = min(max_fee_per_gas, base_fee + max_priority_fee_per_gas)
        base_paid = min(base_fee, max_fee_per_gas)
        tip_paid  = max(0, effective - base_paid)
    """
    gp = getattr(tx, "gas_price", None)
    if gp is not None:
        gas_price = int(gp)
        base_paid = min(gas_price, base_fee)
        tip_paid = max(0, gas_price - base_paid)
        return gas_price, base_paid, tip_paid

    max_fee = getattr(tx, "max_fee_per_gas", None)
    max_tip = getattr(tx, "max_priority_fee_per_gas", None)
    if max_fee is not None and max_tip is not None:
        mf = int(max_fee)
        mt = int(max_tip)
        effective = min(mf, base_fee + mt)
        base_paid = min(base_fee, mf)
        tip_paid = max(0, effective - base_paid)
        return effective, base_paid, tip_paid

    # Fallback: no fee fields? treat as 0 (will be rejected)
    return 0, 0, 0


@dataclass(frozen=True)
class AdmissionResult:
    accept: bool
    reason: str
    floor_with_surge: int
    base_fee_paid: int
    tip_paid: int
    effective_price: int


def admission_check(
    tx,
    *,
    base_fee_floor: int,
    min_tip: int,
) -> AdmissionResult:
    """
    Decide whether to admit a tx given a dynamic floor and tip floor.

    Rules:
      - Compute effective price & split
      - Require effective_price >= base_fee_floor + min_tip
      - Also require tip_paid >= min_tip
      - For 1559-style caps, ensure base component >= min(base_fee_floor, max_fee_per_gas)
    """
    eff, base_paid, tip_paid = effective_gas_price(tx, base_fee=base_fee_floor)
    if eff <= 0:
        return AdmissionResult(
            False, "NoFeeFields", base_fee_floor, base_paid, tip_paid, eff
        )

    # Hard floors
    if eff < (base_fee_floor + min_tip):
        return AdmissionResult(
            False, "BelowFloor", base_fee_floor, base_paid, tip_paid, eff
        )

    if tip_paid < min_tip:
        return AdmissionResult(
            False, "TipTooLow", base_fee_floor, base_paid, tip_paid, eff
        )

    return AdmissionResult(True, "OK", base_fee_floor, base_paid, tip_paid, eff)


# ---------- Convenience wrapper for callers ----------


def decide_and_suggest(
    state: FeeMarketState,
    pressure: MempoolPressure,
    cfg: FeeMarketConfig,
    tx,
) -> Tuple[AdmissionResult, FeeSuggestion]:
    """
    One-shot helper: produce current suggestions AND admission result for a tx.
    """
    sug = suggest_fees(state, pressure, cfg)
    res = admission_check(tx, base_fee_floor=sug.floor_with_surge, min_tip=sug.min_tip)
    return res, sug


# ---------- Pretty printing (debug/metrics hooks) ----------


def summarize(state: FeeMarketState, cfg: FeeMarketConfig) -> str:
    return (
        f"height={state.height} ema_floor={state.ema_floor} "
        f"ema_util={state.ema_util:.3f} fullness_streak={state.fullness_streak} "
        f"min_tip={cfg.min_tip}"
    )


# ---------- Minimal self-test (manual run) ----------

if __name__ == "__main__":
    # Simulate a few blocks with rising utilization then compute suggestion.
    cfg = FeeMarketConfig()
    st = FeeMarketState(ema_floor=3 * GWEI, ema_util=0.5)
    gas_limit = 30_000_000

    for h, used in enumerate(
        [14_000_000, 16_000_000, 18_000_000, 20_000_000, 22_000_000], start=1
    ):
        st = update_on_block(
            st,
            height=h,
            gas_used=used,
            gas_limit=gas_limit,
            cfg=cfg,
            observed_min_accepted_fee=None,
        )
        print("upd:", summarize(st, cfg))

    pressure = MempoolPressure(
        pending_txs=120_000, pending_gas=160_000_000, block_gas_limit=gas_limit
    )
    sug = suggest_fees(st, pressure, cfg)
    print(
        "suggest:",
        dict(
            base_fee=sug.base_fee,
            surge_multiplier=sug.surge_multiplier,
            floor_with_surge=sug.floor_with_surge,
            min_tip=sug.min_tip,
            suggested_legacy_gas_price=sug.suggested_legacy_gas_price,
        ),
    )

    class _T:
        # Example 1559 tx:
        max_fee_per_gas = 200 * GWEI
        max_priority_fee_per_gas = 3 * GWEI
        gas_price = None

    res = admission_check(
        _T(), base_fee_floor=sug.floor_with_surge, min_tip=sug.min_tip
    )
    print("admission:", res)
