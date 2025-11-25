"""
capabilities.bench
==================

Tiny helpers shared by benchmark scripts in this package. These utilities are
purposefully dependency-free and safe to import in constrained environments.

Typical usage (inside a bench script):
    from capabilities.bench import bench_env, warmup, run_bench

    def work(n: int) -> int:
        s = 0
        for i in range(n):
            s ^= (i * 2654435761) & 0xFFFFFFFF
        return s

    if __name__ == "__main__":
        print("env:", bench_env())
        warmup(lambda: work(50_000), iters=50)
        stats = run_bench(lambda: work(50_000), iters=200)
        print(stats)
"""

from __future__ import annotations

import os
import platform
import sys
import time
from statistics import mean
from typing import Any, Callable, Dict, Iterable, Mapping, MutableMapping, Optional, Sequence

__all__ = [
    "bench_env",
    "warmup",
    "run_bench",
]


def bench_env() -> Dict[str, Any]:
    """
    Return a small snapshot of the current runtime environment useful for bench logs.
    """
    return {
        "python": sys.version.split()[0],
        "impl": platform.python_implementation(),
        "platform": platform.platform(aliased=True, terse=True),
        "machine": platform.machine(),
        "cpu_count": os.cpu_count() or 1,
        "time_source": "perf_counter",
    }


def warmup(fn: Callable[[], Any], iters: int = 100) -> None:
    """
    Call `fn` `iters` times without recording timings. Helps warm caches/JITs.
    """
    for _ in range(max(0, int(iters))):
        fn()


def run_bench(fn: Callable[[], Any], iters: int = 100, repeat: int = 1) -> Dict[str, Any]:
    """
    Time `fn` repeatedly and return simple statistics.

    Args:
        fn: Zero-arg callable to measure (wrap args with a lambda if needed).
        iters: Number of iterations per repeat.
        repeat: Number of repeated timing rounds (best-of semantics).

    Returns:
        dict with fields: iters, repeat, samples (list), best, avg, p50, p95, total
        Times are in seconds (float).
    """
    iters = max(1, int(iters))
    repeat = max(1, int(repeat))

    samples = []
    total = 0.0
    for _ in range(repeat):
        t0 = time.perf_counter()
        for _ in range(iters):
            fn()
        dt = time.perf_counter() - t0
        samples.append(dt / iters)
        total += dt

    samples_sorted = sorted(samples)
    idx50 = max(0, int(0.50 * (len(samples_sorted) - 1)))
    idx95 = max(0, int(0.95 * (len(samples_sorted) - 1)))

    return {
        "iters": iters,
        "repeat": repeat,
        "samples": samples,
        "best": min(samples),
        "avg": mean(samples),
        "p50": samples_sorted[idx50],
        "p95": samples_sorted[idx95],
        "total": total,
        "env": bench_env(),
    }
