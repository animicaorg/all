from __future__ import annotations

"""
Assignments/sec vs provider count

This micro-benchmark simulates the scheduler's "match jobs to eligible providers"
hot path using a heap-based chooser (O(log P) per assignment). It measures how
throughput scales with the number of providers.

Design notes
------------
- Providers have a fixed per-round capacity (concurrent lease slots). When all
  capacity is consumed across the fleet, we "renew" leases and start a new round.
- The chooser uses a max-heap on available capacity (tie-broken by provider id)
  to approximate a typical fairness/least-loaded strategy.
- Deterministic by default via --seed (or AICF_BENCH_SEED); no external deps.

Run
---
  python -m aicf.bench.matcher_throughput
  python -m aicf.bench.matcher_throughput --providers 8,16,32,64,128 --jobs 200000
  python -m aicf.bench.matcher_throughput --providers 256 --jobs 500000 --capacity 8 --seed 1337

Environment (optional)
----------------------
AICF_BENCH_WARMUP     : Warmup jobs (default: 0)
AICF_BENCH_ITERATIONS : Multiplier on --jobs (int, default: 1)
AICF_BENCH_SEED       : PRNG seed (int)

Output
------
A simple table:
providers  jobs    capacity  rounds  assigns  elapsed_s  assigns/s  usec/assign
"""

import argparse
import heapq
import math
import os
import random
import sys
import time
from dataclasses import dataclass
from typing import List, Tuple


@dataclass(slots=True)
class Provider:
    pid: int
    capacity: int
    available: int


def _build_heap(providers: List[Provider]) -> List[Tuple[int, int]]:
    """Return a max-heap (as min-heap of negatives) keyed by available slots."""
    heap: List[Tuple[int, int]] = []
    for p in providers:
        # store as (neg_available, pid) to get max-available on pop
        heap.append((-p.available, p.pid))
    heapq.heapify(heap)
    return heap


def assign_jobs(
    num_jobs: int, num_providers: int, capacity: int, seed: int
) -> Tuple[int, float, int]:
    """
    Assign `num_jobs` jobs across `num_providers` providers with per-round capacity `capacity`.
    Returns: (assignments, elapsed_seconds, rounds_performed)
    """
    rnd = random.Random(seed)

    # Initialize providers with deterministic yet slightly variable capacity (±10%)
    providers = [
        Provider(
            pid=i,
            capacity=max(1, int(round(capacity * (0.9 + 0.2 * rnd.random())))),
            available=0,
        )
        for i in range(num_providers)
    ]
    # First round
    for p in providers:
        p.available = p.capacity
    total_capacity = sum(p.capacity for p in providers)
    heap = _build_heap(providers)

    assigned = 0
    rounds = 1
    start = time.perf_counter()

    # Fast path variables to reduce attribute lookups in the loop
    providers_by_id = {p.pid: p}

    while assigned < num_jobs:
        # If fleet is exhausted, start a new round (renew leases)
        if not heap or -heap[0][0] == 0:
            for p in providers:
                p.available = p.capacity
            heap = _build_heap(providers)
            rounds += 1

        neg_avail, pid = heapq.heappop(heap)
        avail = -neg_avail
        if avail <= 0:
            # No capacity here; continue (should be rare due to check above)
            continue

        # "Assign" one job
        assigned += 1
        p = providers_by_id[pid]
        p.available = avail - 1

        # Push back with updated availability
        heapq.heappush(heap, (-p.available, pid))

    elapsed = time.perf_counter() - start
    return assigned, elapsed, rounds


def parse_provider_counts(s: str | None) -> List[int]:
    if not s:
        # Default sweep (powers of two)
        return [1, 2, 4, 8, 16, 32, 64, 128, 256]
    try:
        return [int(x.strip()) for x in s.split(",") if x.strip()]
    except ValueError as e:
        raise argparse.ArgumentTypeError(f"Invalid provider list: {s}") from e


def main(argv: List[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Benchmark assignments/sec vs provider count (heap-based matcher)."
    )
    parser.add_argument(
        "--providers",
        type=str,
        default=None,
        help="Comma-separated list of provider counts to test (e.g., '8,16,32,64'). "
        "Default sweep is 1..256 powers of two.",
    )
    parser.add_argument(
        "--jobs",
        type=int,
        default=200_000,
        help="Total jobs to assign per run (before multipliers).",
    )
    parser.add_argument(
        "--capacity", type=int, default=4, help="Per-provider capacity per round."
    )
    parser.add_argument(
        "--seed",
        type=int,
        default=None,
        help="PRNG seed (default from env AICF_BENCH_SEED or 42).",
    )
    parser.add_argument(
        "--no-header", action="store_true", help="Do not print the header row."
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Emit JSON lines for each run (machine-readable).",
    )

    args = parser.parse_args(argv)

    warmup_jobs = int(os.getenv("AICF_BENCH_WARMUP", "0") or "0")
    iter_mult = int(os.getenv("AICF_BENCH_ITERATIONS", "1") or "1")
    env_seed = os.getenv("AICF_BENCH_SEED")
    seed = args.seed if args.seed is not None else (int(env_seed) if env_seed else 42)

    prov_counts = parse_provider_counts(args.providers)
    base_jobs = max(1, args.jobs)
    total_jobs = base_jobs * max(1, iter_mult)

    if warmup_jobs > 0:
        # Warmup once at median provider count (or first)
        median_p = prov_counts[len(prov_counts) // 2]
        assign_jobs(warmup_jobs, median_p, args.capacity, seed)

    if not args.no_header and not args.json:
        print(
            "providers  jobs      capacity  rounds  assigns     elapsed_s  assigns/s   usec/assign"
        )

    for pcount in prov_counts:
        assigns, elapsed, rounds = assign_jobs(total_jobs, pcount, args.capacity, seed)
        assigns_per_s = assigns / elapsed if elapsed > 0 else float("inf")
        usec_per = (elapsed / assigns) * 1e6 if assigns > 0 else float("inf")

        if args.json:
            import json

            print(
                json.dumps(
                    {
                        "providers": pcount,
                        "jobs": total_jobs,
                        "capacity": args.capacity,
                        "rounds": rounds,
                        "assigns": assigns,
                        "elapsed_s": elapsed,
                        "assigns_per_s": assigns_per_s,
                        "usec_per_assign": usec_per,
                        "seed": seed,
                    }
                )
            )
        else:
            print(
                f"{pcount:9d}  {total_jobs:9d}  {args.capacity:8d}  {rounds:6d}  "
                f"{assigns:8d}  {elapsed:10.6f}  {assigns_per_s:9.0f}  {usec_per:11.2f}"
            )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
