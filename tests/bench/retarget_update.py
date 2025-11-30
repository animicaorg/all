# -*- coding: utf-8 -*-
"""
retarget_update.py
==================

Θ (theta) retarget math throughput.

This benchmark measures how fast we can run a difficulty/threshold retarget loop
over a stream of observed inter-block intervals. It prefers a real
implementation from `consensus.difficulty` when available, and otherwise falls
back to a simple, well-behaved EMA-style reference.

Output: prints a single JSON object (last line) suitable for tests/bench/runner.py.

Example:
    python tests/bench/retarget_update.py --updates 1000000 --target-s 2.0 --alpha 0.1 --repeat 5
"""
from __future__ import annotations

import argparse
import json
import math
import os
import random
import statistics
import time
from typing import Callable, Iterable, List, Optional, Tuple

# Optional numpy acceleration for data generation
try:
    import numpy as np  # type: ignore
except Exception:  # pragma: no cover
    np = None  # type: ignore


# --------------------------- Synthetic Data Gen -------------------------------


def _rng(seed: Optional[int]) -> random.Random:
    if seed is None:
        seed = int(os.environ.get("PYTHONHASHSEED", "0") or "1337")
    return random.Random(seed)


def _gen_intervals_lognormal(
    n: int, target_s: float, sigma: float, seed: Optional[int]
) -> List[float]:
    """
    Generate positive intervals with mean ~= target_s using a lognormal distribution.
    sigma is the log-space stddev (0.0 → deterministic).
    We set mu so that E[exp(X)] = 1, then scale by target_s.
    """
    if sigma <= 0:
        return [target_s] * n

    if np is not None:
        np.random.seed(seed if seed is not None else 1337)
        mu = -0.5 * (sigma**2)
        base = np.random.lognormal(mean=mu, sigma=sigma, size=n)
        return (base * target_s).tolist()
    else:
        # Box-Muller for normal -> exp -> lognormal
        r = _rng(seed)
        out: List[float] = []
        mu = -0.5 * (sigma**2)
        for _ in range(n):
            # approx standard normal
            u1 = max(r.random(), 1e-12)
            u2 = r.random()
            z = math.sqrt(-2.0 * math.log(u1)) * math.cos(2.0 * math.pi * u2)
            ln = math.exp(mu + sigma * z)
            out.append(ln * target_s)
        return out


# ------------------------------ Retarget Fallback -----------------------------


def _retarget_step(
    theta: float,
    dt: float,
    target_s: float,
    alpha: float,
    k: float,
    clamp_up: float,
    clamp_down: float,
    eps: float = 1e-18,
) -> float:
    """
    Single-step multiplicative correction with smoothing:

        rel_err = (target_s - dt) / target_s
        mult    = 1 + k * rel_err
        mult    = clamp(mult, clamp_down, clamp_up)
        proposed = theta * mult
        theta'   = (1 - alpha) * theta + alpha * proposed

    - If dt > target (blocks too slow), rel_err < 0 → mult < 1 → theta decreases.
    - If dt < target (too fast), rel_err > 0 → mult > 1 → theta increases.

    This is directionally correct regardless of the concrete meaning of Θ in your system
    (threshold/target units), as long as "higher θ → more difficult".
    """
    rel_err = (target_s - dt) / target_s
    mult = 1.0 + k * rel_err
    # Clamp per-step adjustment
    mult = max(clamp_down, min(clamp_up, mult))
    proposed = theta * mult
    theta_next = (1.0 - alpha) * theta + alpha * proposed
    if not math.isfinite(theta_next) or theta_next <= 0:
        theta_next = eps
    return theta_next


def retarget_stream_fallback(
    intervals: Iterable[float],
    theta0: float,
    target_s: float,
    alpha: float,
    k: float,
    clamp_up: float,
    clamp_down: float,
) -> float:
    theta = theta0
    for dt in intervals:
        theta = _retarget_step(
            theta, float(dt), target_s, alpha, k, clamp_up, clamp_down
        )
    return theta


# ------------------------------ Optional Import -------------------------------


def _maybe_real_retarget() -> Optional[Callable]:
    """
    Try to import a real retarget loop from consensus.difficulty.
    Supported candidate functions (any signature-compatible):
      - retarget_stream(intervals, theta0, target_s, alpha, k, clamp_up, clamp_down)
      - update_theta_stream(...)
      - retarget_batch(intervals, theta0, target_s, alpha, k, clamp_up, clamp_down)
    """
    try:
        import importlib

        mod = importlib.import_module("consensus.difficulty")
        for name in ("retarget_stream", "update_theta_stream", "retarget_batch"):
            fn = getattr(mod, name, None)
            if callable(fn):
                return fn
    except Exception:
        return None
    return None


# ------------------------------ Benchmark Core --------------------------------


def _timeit(fn: Callable[[], float], repeats: int) -> Tuple[List[float], float]:
    timings: List[float] = []
    last = 0.0
    for _ in range(repeats):
        t0 = time.perf_counter()
        last = float(fn())
        t1 = time.perf_counter()
        timings.append(t1 - t0)
    return timings, last


def run_bench(
    updates: int,
    target_s: float,
    alpha: float,
    k: float,
    clamp_up: float,
    clamp_down: float,
    jitter_sigma: float,
    theta0: float,
    warmup: int,
    repeat: int,
    mode: str,
    seed: Optional[int],
) -> dict:
    # Prepare intervals once; we only measure retarget work
    intervals = _gen_intervals_lognormal(updates, target_s, jitter_sigma, seed)

    # Choose implementation
    fn_real = _maybe_real_retarget() if mode == "consensus" else None
    if fn_real is not None:
        label = "consensus"

        def bench_call() -> float:
            return float(fn_real(intervals, theta0, target_s, alpha, k, clamp_up, clamp_down))  # type: ignore

    else:
        label = "fallback"

        def bench_call() -> float:
            return retarget_stream_fallback(
                intervals, theta0, target_s, alpha, k, clamp_up, clamp_down
            )

    # Warmup
    if warmup > 0:
        _timeit(bench_call, warmup)

    # Measure
    timings, last_theta = _timeit(bench_call, repeat)

    median_s = statistics.median(timings)
    p90_s = (
        statistics.quantiles(timings, n=10)[8] if len(timings) >= 10 else max(timings)
    )
    updates_per_s = (updates * repeat / median_s) if median_s > 0 else float("inf")

    return {
        "case": f"consensus.retarget_theta(updates={updates})",
        "params": {
            "updates": updates,
            "target_s": target_s,
            "alpha": alpha,
            "k": k,
            "clamp_up": clamp_up,
            "clamp_down": clamp_down,
            "jitter_sigma": jitter_sigma,
            "theta0": theta0,
            "repeat": repeat,
            "warmup": warmup,
            "mode": label,
            "seed": (
                seed
                if seed is not None
                else int(os.environ.get("PYTHONHASHSEED", "0") or "1337")
            ),
        },
        "result": {
            "updates_per_s": updates_per_s,
            "median_s": median_s,
            "p90_s": p90_s,
            "final_theta": last_theta,
        },
    }


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Θ retarget math throughput benchmark.")
    ap.add_argument(
        "--updates",
        type=int,
        default=1_000_000,
        help="Number of retarget steps per run (default: 1,000,000)",
    )
    ap.add_argument(
        "--target-s",
        type=float,
        default=2.0,
        dest="target_s",
        help="Target inter-block interval in seconds (default: 2.0)",
    )
    ap.add_argument(
        "--alpha",
        type=float,
        default=0.10,
        help="EMA smoothing factor in [0,1] (default: 0.10)",
    )
    ap.add_argument(
        "--k",
        type=float,
        default=0.50,
        help="Proportional gain for per-step correction (default: 0.50)",
    )
    ap.add_argument(
        "--clamp-up",
        type=float,
        default=1.25,
        dest="clamp_up",
        help="Max multiplicative increase per step (default: 1.25)",
    )
    ap.add_argument(
        "--clamp-down",
        type=float,
        default=0.80,
        dest="clamp_down",
        help="Max multiplicative decrease per step (default: 0.80)",
    )
    ap.add_argument(
        "--jitter-sigma",
        type=float,
        default=0.20,
        dest="jitter_sigma",
        help="Lognormal sigma for interval jitter (default: 0.20)",
    )
    ap.add_argument(
        "--theta0",
        type=float,
        default=1.0,
        help="Initial Θ (dimensionless) (default: 1.0)",
    )
    ap.add_argument(
        "--warmup", type=int, default=1, help="Warmup iterations (default: 1)"
    )
    ap.add_argument(
        "--repeat", type=int, default=5, help="Measured iterations (default: 5)"
    )
    ap.add_argument(
        "--mode",
        choices=("auto", "fallback", "consensus"),
        default="auto",
        help="Use fallback reference or try importing consensus.difficulty (default: auto)",
    )
    ap.add_argument(
        "--seed",
        type=int,
        default=None,
        help="PRNG seed (default: from PYTHONHASHSEED or 1337)",
    )
    args = ap.parse_args(argv)

    mode = args.mode
    if mode == "auto":
        mode = "consensus"  # try real one, fallback if import fails

    payload = run_bench(
        updates=args.updates,
        target_s=args.target_s,
        alpha=args.alpha,
        k=args.k,
        clamp_up=args.clamp_up,
        clamp_down=args.clamp_down,
        jitter_sigma=args.jitter_sigma,
        theta0=args.theta0,
        warmup=args.warmup,
        repeat=args.repeat,
        mode=mode,
        seed=args.seed,
    )

    print(json.dumps(payload, separators=(",", ":"), sort_keys=False))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
