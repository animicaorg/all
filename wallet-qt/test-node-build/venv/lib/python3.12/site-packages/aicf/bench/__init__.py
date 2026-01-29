from __future__ import annotations

"""
AICF Benchmarks

This package hosts small, focused micro/meso-benchmarks for the
AI/Quantum Compute Fabric (enqueue/assign/settle paths, SLA/penalties,
and storage throughput).

Conventions
-----------
- Each benchmark module must define a `main()` entrypoint so it can be run with:
      python -m aicf.bench.<module> [args]
- Modules should avoid side effects at import time; initialize resources in main().
- Use lightweight, stdlib-only instrumentation where possible (time, statistics).
- Prefer deterministic seeds for any pseudo-random inputs.

Environment (optional)
----------------------
AICF_BENCH_DB        : Path to a throwaway DB (default: in-memory if supported).
AICF_BENCH_ITERATIONS: Default iteration count for tight loops (int).
AICF_BENCH_WARMUP    : Warmup iterations before timing (int).

This file exists primarily as a package marker.
"""

from aicf.version import __version__  # re-export for convenience

__all__ = ["__version__"]
