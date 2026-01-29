"""
randomness.bench
----------------

Lightweight package marker for randomness benchmark scripts.
These benches measure performance characteristics like:
- VDF verification throughput
- Commit/reveal aggregation speed
- Storage backend (KV/SQLite/Rocks) read/write latency

Each script in this package is intended to be runnable as a module, e.g.:
    python -m randomness.bench.vdf_verify
"""

from __future__ import annotations

__all__: list[str] = []
