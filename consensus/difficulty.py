"""
Difficulty & Target Schedule (PoIES)
====================================

This module maintains the acceptance threshold Θ (in *micro-nats*) and exposes
helpers to derive *share* thresholds used by miners. It implements a *fractional
retarget* loop with:
  - Exponential Moving Average (EMA) of log(dt / T)
  - Proportional gain (β)
  - Per-step clamps (ΔΘ bounds)
  - Global clamps (Θ_min ≤ Θ ≤ Θ_max)

Model
-----
Block inter-arrival is approximately exponential in the "difficulty parameter"
τ (nats): expected interval ~ const · exp(τ). Our acceptance threshold Θ (µ-nats)
is the discrete representation of τ (Θ = ⌊τ · 1e6⌉). If blocks arrive too slowly
(dt > T), we *decrease* τ/Θ; if too quickly (dt < T), we *increase* τ/Θ.

We update using an EMA of the *log ratio*:

    r_k  = ln( dt_k / T )
    r̂_k = (1-α)^m · r̂_{k-1} + (1 - (1-α)^m) · r_k      (skip-EMA across m blocks)
    τ_{k+1} = τ_k - β · r̂_k
    Θ_{k+1} = clamp_global( clamp_step( Θ_k + round( (τ_{k+1}-τ_k) · 1e6 ) ) )

Where:
  α ∈ (0,1] is derived from a half-life in blocks: α = 1 - 2^(-1/H).
  β ∈ (0,1] is the proportional gain (how aggressively Θ reacts).
  clamp_step limits |ΔΘ| per update; clamp_global pins Θ inside [Θ_min, Θ_max].

Shares
------
A *share threshold* τ_s (nats) is chosen below Θ to target ~K shares per block:

    τ_s = Θ_nats - ln(K)         →  Θ_share = ⌊(Θ_nats - ln K) · 1e6⌉

This follows from the Poisson scaling of exceedances for H(u) = −ln u.

Exports
-------
- RetargetParams, RetargetState
- init_state(params, theta_init_micro)
- update_theta(state, dt_seconds, blocks_skipped=1)
- compute_share_micro(theta_micro, shares_per_block)
- compute_share_tiers(theta_micro, factors=(2,4,8,16,32,64,128,256))
- micro_to_nats, nats_to_micro

All functions are deterministic and side-effect free.

See also:
- consensus.math: H(u) numerics and nats↔µ-nats helpers
- consensus.scorer: acceptance S = H(u)+Σψ ≥ Θ
"""

from __future__ import annotations

import math
from dataclasses import dataclass, replace
from typing import Iterable, Mapping, MutableMapping, Optional, Sequence, Tuple, List, Dict

from .types import MicroNat  # alias for int


# ---------------------------------------------------------------------------
# Utilities
# ---------------------------------------------------------------------------

_MICRO: float = 1_000_000.0


def micro_to_nats(theta_micro: MicroNat) -> float:
    return float(theta_micro) / _MICRO


def nats_to_micro(tau_nats: float) -> MicroNat:
    if not math.isfinite(tau_nats):
        raise ValueError("tau_nats must be finite")
    return int(round(tau_nats * _MICRO))


def _safe_log(x: float) -> float:
    if x <= 0.0 or not math.isfinite(x):
        return 0.0
    return math.log(x)


def _derive_alpha_from_half_life(half_life_blocks: float) -> float:
    """
    α = 1 - 2^(-1/H). α≈0.0433 for H=16; α≈0.0207 for H=32.
    """
    if half_life_blocks <= 0:
        return 1.0
    return 1.0 - 2.0 ** (-1.0 / float(half_life_blocks))


# ---------------------------------------------------------------------------
# Params & State
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class RetargetParams:
    """
    Parameters controlling the Θ retarget loop.

    Attributes
    ----------
    target_block_time_s : float
        Target inter-block time (T).
    half_life_blocks : float
        EMA half-life (in blocks). Larger → smoother.
    gain_beta : float
        Proportional gain β ∈ (0, 1.5] (typ. 0.5–1.0).
    step_clamp_micro : MicroNat
        Per-update absolute clamp on |ΔΘ|.
    theta_min_micro : MicroNat
        Lower bound for Θ (do not go below).
    theta_max_micro : MicroNat
        Upper bound for Θ (do not exceed).
    """
    target_block_time_s: float = 12.0
    half_life_blocks: float = 24.0
    gain_beta: float = 0.75
    step_clamp_micro: MicroNat = 400_000   # ~0.4 nats per step max
    theta_min_micro: MicroNat = 500_000    # ~0.5 nats (very easy)
    theta_max_micro: MicroNat = 30_000_000 # 30 nats (very hard)


@dataclass(frozen=True)
class RetargetState:
    """
    Retarget loop state.

    Attributes
    ----------
    theta_micro : MicroNat
        Current acceptance threshold Θ (µ-nats).
    tau_nats : float
        Floating-point view of Θ in natural units (nats).
    ema_log_dt_over_T : float
        EMA estimate of ln(dt/T).
    alpha : float
        Smoothing factor α used for EMA.
    params : RetargetParams
        The parameter set used to evolve the state.
    """
    theta_micro: MicroNat
    tau_nats: float
    ema_log_dt_over_T: float
    alpha: float
    params: RetargetParams


# ---------------------------------------------------------------------------
# Core API
# ---------------------------------------------------------------------------

def init_state(params: RetargetParams, theta_init_micro: MicroNat) -> RetargetState:
    """Initialize retarget state from params and initial Θ."""
    tau0 = micro_to_nats(theta_init_micro)
    alpha = _derive_alpha_from_half_life(params.half_life_blocks)
    return RetargetState(
        theta_micro=int(theta_init_micro),
        tau_nats=float(tau0),
        ema_log_dt_over_T=0.0,
        alpha=float(alpha),
        params=params,
    )


def update_theta(
    state: RetargetState,
    dt_seconds: float,
    *,
    blocks_skipped: int = 1,
) -> RetargetState:
    """
    Update Θ using an EMA of ln(dt/T) and proportional gain β.

    Parameters
    ----------
    state : RetargetState
        Current loop state.
    dt_seconds : float
        Observed inter-block time for the most recent step (seconds).
        If multiple blocks were skipped (e.g., empty epochs), supply the
        *mean* per-block dt and set blocks_skipped accordingly (defaults to 1).
    blocks_skipped : int
        Number of missing steps to roll the EMA across (≥1). The effective
        smoothing used is α_eff = 1 - (1-α)^m.

    Returns
    -------
    RetargetState
        Updated state with new Θ, τ, and EMA accumulator.
    """
    if dt_seconds <= 0 or not math.isfinite(dt_seconds):
        # Ignore pathological inputs; return state unchanged.
        return state

    p = state.params
    # Sample of ln(dt/T)
    r_k = _safe_log(dt_seconds / max(1e-9, p.target_block_time_s))
    # Skip-aware EMA update: r̂ = (1-α)^m r̂ + (1 - (1-α)^m) r_k
    m = max(1, int(blocks_skipped))
    decay = (1.0 - state.alpha) ** m
    alpha_eff = 1.0 - decay
    r_hat = decay * state.ema_log_dt_over_T + alpha_eff * r_k

    # τ update: τ_{k+1} = τ_k - β · r̂
    tau_next = state.tau_nats - float(p.gain_beta) * r_hat
    # Convert to micro and clamp step
    theta_prev = state.theta_micro
    theta_target_micro = nats_to_micro(tau_next)

    # Per-step clamp
    delta = theta_target_micro - theta_prev
    max_step = int(abs(p.step_clamp_micro))
    if delta > max_step:
        theta_next = theta_prev + max_step
    elif delta < -max_step:
        theta_next = theta_prev - max_step
    else:
        theta_next = theta_target_micro

    # Global clamps
    theta_next = max(int(p.theta_min_micro), min(int(p.theta_max_micro), int(theta_next)))

    return RetargetState(
        theta_micro=int(theta_next),
        tau_nats=micro_to_nats(theta_next),
        ema_log_dt_over_T=float(r_hat),
        alpha=state.alpha,
        params=state.params,
    )


# ---------------------------------------------------------------------------
# Share thresholds
# ---------------------------------------------------------------------------

def compute_share_micro(theta_micro: MicroNat, shares_per_block: float) -> MicroNat:
    """
    Compute a *share* threshold in µ-nats such that the expected number of shares
    per block is approximately `shares_per_block`.

        τ_share = Θ_nats - ln(K)

    where K = shares_per_block and Θ_nats = Θ / 1e6.

    Notes
    -----
    - K must be ≥ 1. For K<1, the threshold would be above Θ (nonsense).
    - We clamp to [0, Θ) in µ-nats.
    """
    K = max(1.0, float(shares_per_block))
    theta_n = micro_to_nats(theta_micro)
    tau_share = theta_n - math.log(K)
    if tau_share < 0.0:
        return 0
    val = nats_to_micro(tau_share)
    return max(0, min(int(theta_micro) - 1, int(val)))


# Compatibility alias expected by legacy callers/tests.
def share_microtarget(theta_micro: MicroNat, shares_per_block: float) -> MicroNat:
    return compute_share_micro(theta_micro, shares_per_block)


def compute_share_tiers(
    theta_micro: MicroNat,
    factors: Sequence[int] = (2, 4, 8, 16, 32, 64, 128, 256),
) -> List[Dict[str, float]]:
    """
    Produce a set of common share tiers parameterized by multiplicative factors K.
    Each entry includes:
      - 'K'                : multiplicative target shares per block
      - 'theta_share_micro': µ-nats threshold for the tier
      - 'd_ratio_min'      : τ_share / Θ  (useful for scorer HASH metrics)
    """
    theta = int(theta_micro)
    theta_n = micro_to_nats(theta)
    out: List[Dict[str, float]] = []
    for K in factors:
        tau_s = max(0.0, theta_n - math.log(max(1.0, float(K))))
        th_s = nats_to_micro(tau_s)
        if th_s >= theta:
            th_s = theta - 1
            tau_s = micro_to_nats(th_s)
        d_ratio = 0.0 if theta <= 0 else (tau_s / theta_n if theta_n > 0 else 0.0)
        out.append(
            {
                "K": float(K),
                "theta_share_micro": float(th_s),
                "d_ratio_min": float(max(0.0, min(1.0, d_ratio))),
            }
        )
    return out


# ---------------------------------------------------------------------------
# Convenience: multi-sample EMA update
# ---------------------------------------------------------------------------

def update_theta_multi(
    state: RetargetState,
    dt_seconds_samples: Sequence[float],
) -> RetargetState:
    """
    Fold multiple dt samples (e.g., from multiple recent blocks) into state,
    applying the same EMA parameters sequentially. Equivalent to successive
    calls to `update_theta` with blocks_skipped=1.
    """
    s = state
    for dt in dt_seconds_samples:
        s = update_theta(s, dt_seconds=dt, blocks_skipped=1)
    return s


# ---------------------------------------------------------------------------
# Self-test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    # Simulate a short series where blocks are 20% faster than target for a while,
    # then 30% slower, and observe Θ react smoothly within clamps.
    params = RetargetParams(
        target_block_time_s=12.0,
        half_life_blocks=24.0,   # ~smooth over a day at 12s blocks
        gain_beta=0.9,
        step_clamp_micro=500_000,
        theta_min_micro=800_000,
        theta_max_micro=20_000_000,
    )
    s = init_state(params, theta_init_micro=3_000_000)  # ~3.0 nats

    def show(tag: str, st: RetargetState):
        tiers = compute_share_tiers(st.theta_micro, (4, 16, 64, 256))
        print(f"{tag}: Θ={st.theta_micro/1e6:.6f} nats  r̂={st.ema_log_dt_over_T:+.4f}  "
              f"K16 τ_share={tiers[1]['theta_share_micro']/1e6:.6f} nats")

    # 40 blocks at 9.6s (fast)
    for i in range(40):
        s = update_theta(s, dt_seconds=9.6)
        if i in (0, 9, 19, 39):
            show(f"fast{i+1}", s)

    # 40 blocks at 15.6s (slow)
    for i in range(40):
        s = update_theta(s, dt_seconds=15.6)
        if i in (0, 9, 19, 39):
            show(f"slow{i+1}", s)

    # Print share tiers for the final Θ
    tiers = compute_share_tiers(s.theta_micro)
    print("tiers(K, θ_share, d_ratio):", [(t['K'], round(t['theta_share_micro']/1e6, 4), round(t['d_ratio_min'], 3)) for t in tiers])
