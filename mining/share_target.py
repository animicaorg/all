from __future__ import annotations

"""
Share-difficulty helpers for the HashShare component of PoIES.

We work in µ-nats (micro-nats) for thresholds:
  - Θ (theta) is the consensus acceptance threshold for a block (in µ-nats)
  - t_share is the *share* threshold used by miners to emit frequent, lighter
    proofs (in µ-nats), with t_share <= Θ.

Background:
  u ~ Uniform(0,1], H(u) = -ln(u) ~ Exp(λ=1).
  Pr[H(u) >= t] = e^{-t}. Expected trials for threshold t is E[trials] = e^{t}.

We keep share rates roughly stable across difficulty by defining a constant
ratio R between the share probability and the block probability:

    R := Pr[share] / Pr[block]  = e^{-t_share} / e^{-Θ} = e^{-(t_share - Θ)}

Solving for t_share:

    t_share = Θ - ln(R)

R >= 1 (R = 1 → t_share = Θ → "shares" are just blocks).
Typical choices: R = 2^10 … 2^20 depending on desired share frequency.

This module provides:
  - theta_to_expected_trials, threshold_for_expected_trials
  - share_threshold_from_ratio(Θ, R) & share_ratio_from_threshold(Θ, t_share)
  - is_share(Hµ, t_shareµ) and is_block_win(Hµ, Θµ)
  - d_ratio(Hµ, Θµ) — the difficulty ratio H/Θ used by scoring/telemetry
  - calibration helpers to aim for target shares/sec given hashrate

All functions are pure and side-effect free.
"""

import math
from dataclasses import dataclass


MICRO = 1_000_000  # µ-nats per nat


# ─────────────────────────────────────────────────────────────────────────────
# Conversions & basic exponentials in µ-nats
# ─────────────────────────────────────────────────────────────────────────────

def nats_to_micro(n: float) -> int:
    """float nats → int µ-nats (rounded)."""
    return int(round(n * MICRO))


def micro_to_nats(m: int) -> float:
    """int µ-nats → float nats."""
    return m / MICRO


def exp_from_micro(m: int) -> float:
    """Compute e^{m / 1e6} safely for typical Θ (well within double range)."""
    return math.exp(micro_to_nats(m))


def ln_to_micro(x: float) -> int:
    """Compute ln(x) in µ-nats."""
    if x <= 0.0:
        raise ValueError("ln_to_micro: x must be > 0")
    return nats_to_micro(math.log(x))


# ─────────────────────────────────────────────────────────────────────────────
# Trial counts & thresholds
# ─────────────────────────────────────────────────────────────────────────────

def theta_to_expected_trials(theta_micro: int) -> float:
    """
    Expected trials to *win a block* at threshold Θ.
    E[trials] = e^{Θ}.
    """
    return exp_from_micro(theta_micro)


def threshold_for_expected_trials(expected_trials: float) -> int:
    """
    Invert the relationship E[trials] = e^{t} to get a threshold t (µ-nats).
    """
    if expected_trials <= 1.0:
        # ≤1 expected trial → threshold near 0
        return 0
    return ln_to_micro(expected_trials)


# ─────────────────────────────────────────────────────────────────────────────
# Share thresholds from a ratio to block probability
# ─────────────────────────────────────────────────────────────────────────────

def share_threshold_from_ratio(theta_micro: int, share_ratio: float) -> int:
    """
    Compute t_share = Θ - ln(R), where R = Pr[share] / Pr[block] ≥ 1.

    If share_ratio is extremely large such that t_share would be negative,
    clamp to zero because negative thresholds would imply Pr > 1.
    """
    if share_ratio < 1.0:
        raise ValueError("share_ratio must be ≥ 1.0")
    t_share = theta_micro - ln_to_micro(share_ratio)
    return max(0, t_share)


def share_ratio_from_threshold(theta_micro: int, t_share_micro: int) -> float:
    """
    Inverse of share_threshold_from_ratio:
      R = exp(Θ - t_share)
    """
    if t_share_micro < 0:
        raise ValueError("t_share_micro must be ≥ 0")
    return math.exp(micro_to_nats(theta_micro - t_share_micro))


# ─────────────────────────────────────────────────────────────────────────────
# Acceptance predicates
# ─────────────────────────────────────────────────────────────────────────────

def is_block_win(H_micro: int, theta_micro: int) -> bool:
    """
    Pure hashshare block-winner check (without auxiliary ψ).
    True if H(u) ≥ Θ.
    """
    return H_micro >= theta_micro


def is_share(H_micro: int, t_share_micro: int) -> bool:
    """
    Share acceptance check: True if H(u) ≥ t_share.
    """
    return H_micro >= t_share_micro


def d_ratio(H_micro: int, theta_micro: int) -> float:
    """
    Difficulty ratio used by HashShare metrics: d = H / Θ (unitless).
    Returns 0 if Θ=0 to avoid division-by-zero for degenerate configs.
    """
    if theta_micro <= 0:
        return 0.0
    return H_micro / float(theta_micro)


# ─────────────────────────────────────────────────────────────────────────────
# Calibrations for UX: pick t_share for a target shares/sec, given hashrate
# ─────────────────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class ShareCalib:
    t_share_micro: int
    expected_trials_per_share: float
    expected_shares_per_sec: float
    share_ratio: float  # relative to block probability: R = Pr[share]/Pr[block]


def calibrate_share_threshold_by_rate(
    *,
    hashrate_hps: float,
    target_shares_per_sec: float,
    theta_micro: int,
) -> ShareCalib:
    """
    Given an estimated hashrate (hashes per second) and a desired share *rate*
    (shares/second), compute a t_share that yields the requested rate.

      shares/sec  ~=  hashrate / e^{t_share}
      t_share     ~=  ln(hashrate / shares/sec)

    We also report R = Pr[share]/Pr[block] = e^{Θ - t_share} for dashboarding.
    """
    if hashrate_hps <= 0.0:
        raise ValueError("hashrate_hps must be > 0")
    if target_shares_per_sec <= 0.0:
        raise ValueError("target_shares_per_sec must be > 0")

    expected_trials = hashrate_hps / target_shares_per_sec
    t_share = threshold_for_expected_trials(expected_trials)
    # Clamp to a sane floor (0 µ-nats)
    t_share = max(0, t_share)

    exp_t = exp_from_micro(t_share)
    shares_per_sec = hashrate_hps / exp_t
    R = math.exp(micro_to_nats(theta_micro - t_share))

    return ShareCalib(
        t_share_micro=t_share,
        expected_trials_per_share=exp_t,
        expected_shares_per_sec=shares_per_sec,
        share_ratio=R,
    )


def calibrate_share_threshold_by_ratio(
    *,
    theta_micro: int,
    share_ratio: float,
) -> ShareCalib:
    """
    Convenience: pick t_share from a fixed ratio R to block probability.
    This keeps share frequency stable *relative* to block difficulty.

    Example:
      share_ratio = 2**16  → ~65,536 shares per expected block (network-wide).
    """
    t_share = share_threshold_from_ratio(theta_micro, share_ratio)
    exp_t = exp_from_micro(t_share)
    # Shares/sec depends on hashrate; we return NaN to indicate "rate depends on H/s"
    return ShareCalib(
        t_share_micro=t_share,
        expected_trials_per_share=exp_t,
        expected_shares_per_sec=float("nan"),
        share_ratio=share_ratio,
    )


# ─────────────────────────────────────────────────────────────────────────────
# Demo (manual smoke)
# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":  # pragma: no cover
    # Toy Θ ≈ ln(2**32) ≈ 22.1807097779 nats → 22_180_710 µ-nats
    theta_bits = 32
    theta_nat = theta_bits * math.log(2.0)
    theta_micro = nats_to_micro(theta_nat)

    # Choose R = 2**20 shares per expected block
    R = float(2 ** 20)
    t_share = share_threshold_from_ratio(theta_micro, R)

    print("Θ (µ-nats):", theta_micro)
    print("t_share(µ-nats):", t_share)
    print("R back-calc:", share_ratio_from_threshold(theta_micro, t_share))
    print("E[trials]/share:", int(exp_from_micro(t_share)))
    print("E[trials]/block:", int(exp_from_micro(theta_micro)))
