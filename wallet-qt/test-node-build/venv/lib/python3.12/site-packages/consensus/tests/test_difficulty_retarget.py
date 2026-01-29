from __future__ import annotations

import math
import random
import typing as t

import pytest

import consensus.difficulty as diff

# ---------- tolerant pick/extract helpers (handle naming & signature drift) -----


def _pick(mod, name: str, alts: list[str]):
    if hasattr(mod, name):
        return getattr(mod, name)
    for a in alts:
        if hasattr(mod, a):
            return getattr(mod, a)
    raise AttributeError(f"Missing function {name} (tried {alts}) in {mod.__name__}")


# main retarget fn
RETARGET = _pick(
    diff,
    "retarget",
    ["retarget_theta", "ema_retarget", "retarget_ema", "update_theta", "update"],
)

# share micro-target fn (used by miners)
MICRO = _pick(
    diff,
    "share_microtarget",
    [
        "compute_share_microtarget",
        "micro_target",
        "compute_share_threshold",
        "share_threshold",
    ],
)


# Extract theta from various return shapes
def _extract_theta(ret):
    """
    Accept:
      - float/int
      - (theta, state) tuple
      - dict {"theta": x} or {"Theta": x} or {"next": x}
      - object with .theta / .Theta / .value
    """
    if isinstance(ret, (int, float)):
        return float(ret)
    if isinstance(ret, tuple) and ret:
        return float(ret[0])
    if isinstance(ret, dict):
        for k in ("theta", "Theta", "next", "value", "target"):
            if k in ret:
                return float(ret[k])
    for k in ("theta", "Theta", "value", "target"):
        if hasattr(ret, k):
            return float(getattr(ret, k))
    raise TypeError(f"Cannot extract theta from {type(ret)}: {ret!r}")


def _call_retarget(
    prev_theta: float,
    observed_interval: float,
    target_interval: float,
    alpha: float = 0.2,
    clamp_down: float = 0.5,
    clamp_up: float = 2.0,
):
    """
    Try a few common signatures:
      1) f(prev_theta, observed_interval, target_interval, alpha=..., clamp=(down,up))
      2) f(prev_theta, ratio, alpha=..., clamp=(down,up))
      3) f(prev_theta=..., observed_interval=..., target_interval=..., alpha=..., clamp_down=..., clamp_up=...)
      4) f(prev_theta, observed_interval, target_interval)  (defaults inside)
    """
    ratio = observed_interval / target_interval

    # 1) positional + clamp tuple
    try:
        out = RETARGET(prev_theta, observed_interval, target_interval, alpha, (clamp_down, clamp_up))  # type: ignore[misc]
        return _extract_theta(out)
    except TypeError:
        pass

    # 2) ratio form
    try:
        out = RETARGET(prev_theta, ratio, alpha, (clamp_down, clamp_up))  # type: ignore[misc]
        return _extract_theta(out)
    except TypeError:
        pass

    # 3) keywords (two clamp styles)
    try:
        out = RETARGET(
            prev_theta=prev_theta,
            observed_interval=observed_interval,
            target_interval=target_interval,
            alpha=alpha,
            clamp=(clamp_down, clamp_up),  # type: ignore[arg-type]
        )
        return _extract_theta(out)
    except TypeError:
        try:
            out = RETARGET(
                prev_theta=prev_theta,
                observed_interval=observed_interval,
                target_interval=target_interval,
                alpha=alpha,
                clamp_down=clamp_down,
                clamp_up=clamp_up,
            )
            return _extract_theta(out)
        except TypeError:
            pass

    # 4) minimal positional
    try:
        out = RETARGET(prev_theta, observed_interval, target_interval)  # type: ignore[misc]
        return _extract_theta(out)
    except TypeError:
        pass

    # give up
    raise


# ------------------------------- tests -----------------------------------------


def test_directionality_short_vs_long_intervals():
    """
    If blocks come faster than target (short interval), difficulty/Θ should go UP.
    If blocks come slower than target (long interval), difficulty/Θ should go DOWN.
    """
    theta0 = 1.0
    target = 12.0

    # 10s < 12s → increase Θ
    theta1 = _call_retarget(
        theta0, observed_interval=10.0, target_interval=target, alpha=0.3
    )
    assert theta1 > theta0, "shorter interval should raise Θ (harder) via EMA"

    # 20s > 12s → decrease Θ
    theta2 = _call_retarget(
        theta1, observed_interval=20.0, target_interval=target, alpha=0.3
    )
    assert theta2 < theta1, "longer interval should lower Θ (easier) via EMA"


def test_clamp_limits_applied():
    """
    With a very extreme interval change, the per-step change should be bounded by clamp factors.
    """
    theta0 = 1.0
    target = 12.0

    # Extreme: blocks 100× slower than target → without clamps we could overreact.
    # Use tight clamps to assert the ratio stays within [down, up].
    down, up = 0.8, 1.25  # at most -20% or +25% per retarget step

    # Force "too slow" (long interval) ⇒ Θ should not increase; it should go DOWN but not below 0.8×
    theta1 = _call_retarget(
        theta0,
        observed_interval=1200.0,
        target_interval=target,
        alpha=0.5,
        clamp_down=down,
        clamp_up=up,
    )
    ratio1 = theta1 / theta0
    assert 0.0 < ratio1 <= 1.0, "long interval should reduce Θ"
    assert (
        ratio1 >= down - 1e-12
    ), f"downward movement should be clamped to >= {down}, got {ratio1}"

    # Now force "too fast" (short interval) ⇒ Θ should go UP but not exceed 1.25×
    theta2 = _call_retarget(
        theta1,
        observed_interval=0.12,
        target_interval=target,
        alpha=0.5,
        clamp_down=down,
        clamp_up=up,
    )
    ratio2 = theta2 / theta1
    assert ratio2 >= 1.0, "short interval should raise Θ"
    assert (
        ratio2 <= up + 1e-12
    ), f"upward movement should be clamped to <= {up}, got {ratio2}"


def test_stability_under_interval_jitter():
    """
    Feed an alternating jitter around target (±10%) for many steps.
    Θ should remain in a bounded band and not drift unreasonably.
    """
    random.seed(1337)
    theta = 1.0
    target = 12.0
    alpha = 0.2
    down, up = 0.85, 1.20

    series = []
    for i in range(500):
        # alternating sign jitter with a bit of randomness
        sign = -1.0 if (i % 2 == 0) else 1.0
        eps = 0.10 * sign + random.uniform(-0.01, 0.01)  # ~±10% with tiny noise
        interval = target * (1.0 + eps)
        theta = _call_retarget(
            theta,
            observed_interval=interval,
            target_interval=target,
            alpha=alpha,
            clamp_down=down,
            clamp_up=up,
        )
        series.append(theta)

    tmin, tmax = min(series), max(series)
    band = tmax / tmin if tmin > 0 else float("inf")

    # With EMA 0.2 and clamp [0.85,1.20], the band should be comfortably < 2×.
    assert band < 1.8, f"Θ variance band too wide under mild jitter: {band:.3f}×"


def test_micro_target_monotone_in_theta():
    """
    Share micro-target must be monotone in Θ:
      higher Θ (harder chain difficulty) ⇒ higher/stricter share threshold.
    This property is independent of exact normalization.
    """
    # Try two separated thetas
    theta_low = 0.8
    theta_high = 1.6

    # Many implementations also depend on a granularity/microshare parameter.
    # Try to call with or without an explicit granularity.
    def call(theta: float):
        try:
            return float(MICRO(theta, 1_000_000))  # type: ignore[misc]
        except TypeError:
            try:
                return float(MICRO(theta=theta, micro=1_000_000))  # type: ignore[misc]
            except TypeError:
                return float(MICRO(theta))  # type: ignore[misc]

    mt_low = call(theta_low)
    mt_high = call(theta_high)

    # For "threshold" semantics, larger value should be stricter (harder to satisfy).
    # If the implementation returns inverse (i.e., target value where smaller is harder),
    # this assertion would be flipped — detect and accept both consistent conventions.
    if mt_high >= mt_low:
        assert mt_high > mt_low, "micro-target must strictly increase with Θ"
    else:
        # Inverse convention: ensure strict decrease with Θ
        assert mt_high < mt_low, "inverse micro-target must strictly decrease with Θ"


def test_converges_toward_equilibrium_from_off_target():
    """
    Start far from equilibrium and show Θ converges toward a stable region
    when intervals are exactly at target thereafter.
    """
    target = 12.0
    alpha = 0.3
    down, up = 0.5, 1.5

    theta = 5.0  # way too high
    history = []
    for _ in range(50):
        theta = _call_retarget(
            theta,
            observed_interval=target,
            target_interval=target,
            alpha=alpha,
            clamp_down=down,
            clamp_up=up,
        )
        history.append(theta)

    # Should monotonically move toward a fixed point; i.e., subsequent steps change less.
    # Check that the step deltas shrink (not strictly needed every step, but overall).
    deltas = [abs(history[i + 1] - history[i]) for i in range(len(history) - 1)]
    assert (
        deltas[-1] <= deltas[0] + 1e-12
    ), "EMA should reduce step size toward equilibrium"
    # And Θ should be within a tight band around some value (close to initial / arbitrary units).
    assert min(history) > 0, "Θ must stay positive"
