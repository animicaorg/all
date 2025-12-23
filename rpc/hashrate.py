from __future__ import annotations

import math

try:  # Prefer the consensus math if available.
    from mining.share_target import theta_to_expected_trials as _theta_to_expected_trials
except Exception:  # pragma: no cover - fallback when mining module is unavailable

    def _theta_to_expected_trials(theta_micro: int) -> float:
        if theta_micro <= 0:
            return 0.0
        return math.exp(theta_micro / 1_000_000)


def difficulty_to_work(theta_micro: int) -> float:
    """
    Convert Θ (micro-nats) to expected trials per block (work estimate).

    For HashShare, expected trials for threshold Θ is exp(Θ), where Θ is in nats.
    """
    if theta_micro <= 0:
        return 0.0
    try:
        return float(_theta_to_expected_trials(int(theta_micro)))
    except OverflowError:
        return float("inf")

HASHSHARE_TRIALS = 2**32


def work_to_hashshare_rate(trials_per_sec: float) -> float:
    """
    Convert expected trials/sec into HashShare/sec (HS/s).

    HashShare is a higher-order unit so values remain readable for large networks.
    """
    if trials_per_sec <= 0.0:
        return 0.0
    return float(trials_per_sec) / float(HASHSHARE_TRIALS)


__all__ = ["difficulty_to_work", "work_to_hashshare_rate", "HASHSHARE_TRIALS"]
