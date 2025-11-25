# -*- coding: utf-8 -*-
"""
consensus_score.py
==================

PoIES scoring ops/sec on synthetic batches.

This benchmark measures the throughput of a simplified PoIES-style acceptance
predicate over synthetic ψ-vectors. It does **not** require the full consensus
module to be present and falls back to a minimal scorer. If your repository
exposes a faster vectorized scorer, you can optionally plug it in via
--scorer=consensus to import it.

Output: prints a single JSON object (last line) suitable for tests/bench/runner.py.

Example:
    python tests/bench/consensus_score.py --batch 200_000 --kinds 4 --repeat 5
"""
from __future__ import annotations

import argparse
import json
import math
import os
import random
import statistics
import time
from typing import Callable, List, Optional, Tuple

# Optional numpy acceleration
try:
    import numpy as np  # type: ignore
except Exception:  # pragma: no cover
    np = None  # type: ignore


# --------------------------- Synthetic Data Gen -------------------------------

def _rng(seed: Optional[int]) -> random.Random:
    if seed is None:
        # Stable default if PYTHONHASHSEED provided; else fixed constant.
        seed = int(os.environ.get("PYTHONHASHSEED", "0")) or 1337
    r = random.Random(seed)
    return r


def _gen_batch_uniform(
    r: random.Random, batch: int, kinds: int, theta: float, spread: float
) -> Tuple[Optional["np.ndarray"], List[List[float]]]:
    """
    Generate a batch of ψ-vectors. We first sample a total S around θ (uniform in
    [θ - spread, θ + spread]) to target ~50% acceptance, then split S across K kinds
    by sampling a Dirichlet-like slice and multiplying by S.

    Returns (np_array or None, list_fallback) for numpy and pure-Python paths.
    """
    if np is not None:
        # Vectorized path
        # Sample totals around theta
        totals = (theta - spread) + (2.0 * spread) * np.random.random_sample((batch, 1))
        # Dirichlet-like weights using exponential samples
        expo = np.random.exponential(scale=1.0, size=(batch, kinds))
        weights = expo / np.sum(expo, axis=1, keepdims=True)
        psi = weights * totals  # shape (batch, kinds)
        return psi, []
    else:
        data: List[List[float]] = []
        for _ in range(batch):
            tot = (theta - spread) + (2.0 * spread) * r.random()
            # Dirichlet via K exponential(1.0)
            weights = [ -math.log(max(1e-12, 1.0 - r.random())) for _ in range(kinds) ]
            s = sum(weights) or 1.0
            row = [ (w / s) * tot for w in weights ]
            data.append(row)
        return None, data


# ------------------------------ Scorers ---------------------------------------

def _minimal_scorer_python(rows: List[List[float]], theta: float) -> int:
    """Pure-Python acceptance: accept if Σψ >= θ."""
    acc = 0
    for row in rows:
        if sum(row) >= theta:
            acc += 1
    return acc


def _minimal_scorer_numpy(arr: "np.ndarray", theta: float) -> int:  # type: ignore
    """NumPy acceptance: accept if Σψ >= θ."""
    return int(np.sum(np.sum(arr, axis=1) >= theta))


def _maybe_consensus_scorer() -> Optional[Callable]:
    """
    If available, import a real scorer from consensus.scorer.
    We try a few likely names; fall back to None if not found.
    """
    try:
        import importlib

        mod = importlib.import_module("consensus.scorer")
        # Candidate callables we might expose in that module
        for name in ("score_batch", "score_batch_poies", "accept_batch", "benchmark_scorer"):
            fn = getattr(mod, name, None)
            if callable(fn):
                return fn
    except Exception:
        return None
    return None


# ------------------------------ Benchmark -------------------------------------

def _timeit(fn: Callable[[], int], repeats: int) -> Tuple[List[float], int]:
    """
    Run fn() multiple times, collecting durations in seconds.
    Returns (durations, last_result) where last_result is the final integer returned by fn.
    """
    timings: List[float] = []
    last = 0
    for _ in range(repeats):
        t0 = time.perf_counter()
        last = fn()
        t1 = time.perf_counter()
        timings.append(t1 - t0)
    return timings, last


def run_bench(
    batch: int,
    kinds: int,
    theta: float,
    spread: float,
    warmup: int,
    repeat: int,
    scorer_mode: str,
    seed: Optional[int],
) -> dict:
    # Seed both RNGs
    r = _rng(seed)
    if np is not None:
        np.random.seed(seed if seed is not None else 1337)

    # Prepare data once (measure scoring, not generation)
    arr, rows = _gen_batch_uniform(r, batch, kinds, theta, spread)

    # Choose scorer
    scorer_fn_real = None
    if scorer_mode == "consensus":
        scorer_fn_real = _maybe_consensus_scorer()

    if scorer_fn_real is not None:
        # Wrap the real scorer into a zero-arg callable for timing
        def call_real() -> int:
            # Try to pass numpy arrays if supported; otherwise, convert to list
            data = arr if arr is not None else rows
            return int(scorer_fn_real(data=data, theta=theta))  # type: ignore
        bench_fn = call_real
        scorer_label = "consensus"
    else:
        if arr is not None:
            def bench_fn() -> int:
                return _minimal_scorer_numpy(arr, theta)
            scorer_label = "minimal.numpy"
        else:
            def bench_fn() -> int:
                return _minimal_scorer_python(rows, theta)
            scorer_label = "minimal.python"

    # Warmup
    if warmup > 0:
        _timeit(bench_fn, warmup)

    # Measure
    timings, last_result = _timeit(bench_fn, repeat)

    # Stats
    median_s = statistics.median(timings)
    p90_s = statistics.quantiles(timings, n=10)[8] if len(timings) >= 10 else max(timings)
    total_ops = batch * repeat
    ops_per_s = (total_ops / median_s) if median_s > 0 else float('inf')

    result = {
        "case": f"consensus.score_poies(batch={batch},kinds={kinds})",
        "params": {
            "batch": batch,
            "kinds": kinds,
            "theta": theta,
            "spread": spread,
            "repeat": repeat,
            "warmup": warmup,
            "scorer": scorer_label,
            "seed": seed if seed is not None else int(os.environ.get("PYTHONHASHSEED", "0") or "1337"),
        },
        "result": {
            "ops_per_s": ops_per_s,
            "median_s": median_s,
            "p90_s": p90_s,
            "accepted_last_run": last_result
        }
    }
    return result


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="PoIES scoring ops/sec on synthetic batches.")
    ap.add_argument("--batch", type=int, default=100_000, help="Number of ψ-vectors per run (default: 100000)")
    ap.add_argument("--kinds", type=int, default=4, help="Number of proof kinds composing ψ (default: 4)")
    ap.add_argument("--theta", type=float, default=1.0, help="Acceptance threshold Θ (default: 1.0)")
    ap.add_argument("--spread", type=float, default=0.5, help="Uniform spread around Θ for totals (default: 0.5)")
    ap.add_argument("--warmup", type=int, default=1, help="Warmup iterations (default: 1)")
    ap.add_argument("--repeat", type=int, default=5, help="Measured iterations (default: 5)")
    ap.add_argument(
        "--scorer",
        choices=("auto", "minimal", "consensus"),
        default="auto",
        help="Use minimal scorer or attempt to import consensus.scorer (default: auto)"
    )
    ap.add_argument("--seed", type=int, default=None, help="PRNG seed (default: from PYTHONHASHSEED or 1337)")
    args = ap.parse_args(argv)

    # Normalize scorer mode
    mode = args.scorer
    if mode == "auto":
        mode = "consensus"  # try real first; will fall back if not importable

    payload = run_bench(
        batch=args.batch,
        kinds=args.kinds,
        theta=args.theta,
        spread=args.spread,
        warmup=args.warmup,
        repeat=args.repeat,
        scorer_mode=mode,
        seed=args.seed,
    )

    # Print *only* JSON (runner extracts last JSON object)
    print(json.dumps(payload, separators=(",", ":"), sort_keys=False))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
