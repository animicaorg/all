# -*- coding: utf-8 -*-
"""
aicf_matcher.py
===============

Benchmark AICF-style matching throughput (jobs/sec vs providers) and SLA
evaluation speed (evaluations/sec).

This script is **self-contained** and does not require the real `aicf/` package.
If the real modules are present, you can extend the adapters below to use them.
By default we run a synthetic but faithful workload:

- Matching: N providers with quotas/capacity; M incoming jobs with priority.
  We assign jobs to eligible providers (round-robin + simple eligibility).
  The focus is on throughput of the core loop (prioritize, scan, assign).

- SLA evaluation: For a synthetic set of completed jobs, compute pass/fail
  decisions based on traps_ratio, QoS, latency and availability thresholds.

Output: one JSON line aggregating both sub-benchmarks so tests/bench/runner.py
can ingest it.

Examples:
    python tests/bench/aicf_matcher.py
    python tests/bench/aicf_matcher.py --providers 1000 --jobs 20000 --quota 16
    python tests/bench/aicf_matcher.py --match-seconds 3 --sla-seconds 3 --seed 42
"""
from __future__ import annotations

import argparse
import json
import os
import time
from dataclasses import dataclass
from typing import List, Optional, Tuple

# -----------------------------------------------------------------------------
# Deterministic PRNG (tiny LCG) and helpers
# -----------------------------------------------------------------------------


def _lcg_next(x: int) -> int:
    a = 6364136223846793005
    c = 1442695040888963407
    return (a * x + c) & ((1 << 64) - 1)


def _u32(x: int) -> int:
    return (x >> 32) & 0xFFFFFFFF


def _u8(x: int) -> int:
    return (x >> 56) & 0xFF


def _rand_float01(x: int) -> Tuple[int, float]:
    """
    Return (new_state, float in [0,1)).
    """
    nx = _lcg_next(x)
    return nx, ((_u32(nx)) / 4294967296.0)


# -----------------------------------------------------------------------------
# Synthetic AICF models
# -----------------------------------------------------------------------------


@dataclass
class Provider:
    pid: int
    stake: int  # arbitrary units
    max_concurrent: int  # concurrent lease capacity (quota)
    health: float  # 0..1
    region: int  # small integer tag
    avail: int  # mutable per-iteration counter


@dataclass
class Job:
    jid: int
    kind: int  # 0=AI, 1=Quantum
    fee_units: int  # priority component
    size_units: int  # cost/size hint
    age_ticks: int  # larger = older = higher priority


def gen_providers(n: int, quota: int, seed: int) -> List[Provider]:
    x = seed or 1
    out: List[Provider] = []
    for i in range(n):
        x = _lcg_next(x)
        stake = 1_000 + (_u32(x) % 1_000_000)
        x, health = _rand_float01(x)
        region = _u8(x) % 8
        max_concurrent = max(1, quota + int((health - 0.5) * 2.0 * quota * 0.25))
        out.append(
            Provider(
                pid=i,
                stake=stake,
                max_concurrent=max_concurrent,
                health=health,
                region=region,
                avail=max_concurrent,
            )
        )
    return out


def gen_jobs(m: int, seed: int) -> List[Job]:
    x = (seed ^ 0xA55AA55AA55AA55A) & ((1 << 64) - 1) or 1
    out: List[Job] = []
    for j in range(m):
        x = _lcg_next(x)
        kind = _u8(x) & 1  # mix AI/Quantum ~50/50
        x = _lcg_next(x)
        fee_units = 1 + (_u32(x) % 10_000)
        x = _lcg_next(x)
        size_units = 1 + (_u32(x) % 2_000)
        x = _lcg_next(x)
        age_ticks = _u8(x)
        out.append(
            Job(
                jid=j,
                kind=kind,
                fee_units=fee_units,
                size_units=size_units,
                age_ticks=age_ticks,
            )
        )
    # Pre-sort by a simple priority heuristic: fee/size + age
    out.sort(
        key=lambda jb: (jb.fee_units / max(1, jb.size_units)) + (jb.age_ticks * 0.01),
        reverse=True,
    )
    return out


# -----------------------------------------------------------------------------
# Matching benchmark
# -----------------------------------------------------------------------------


def _reset_avail(providers: List[Provider]) -> None:
    for p in providers:
        p.avail = p.max_concurrent


def _eligible(p: Provider, job: Job) -> bool:
    # Simple, branch-light eligibility: health threshold and availability
    if p.avail <= 0:
        return False
    # Prefer providers with decent health; allow slightly lower health for "AI" vs "Quantum"
    min_h = 0.60 if job.kind == 1 else 0.50
    return p.health >= min_h


def run_match_bench(
    seconds: float,
    providers: List[Provider],
    jobs: List[Job],
) -> dict:
    """
    Round-robin scan of providers while walking pre-sorted jobs list.
    Each assignment decrements provider.avail. When a provider reaches 0, it
    becomes ineligible until the next outer-cycle reset (keeps pressure high).
    """
    nprov = len(providers)
    if nprov == 0:
        return {
            "assignments": 0,
            "elapsed_s": 0.0,
            "assignments_per_s": 0.0,
            "utilization": 0.0,
        }

    _reset_avail(providers)
    assigned_total = 0
    scans = 0
    idx = 0  # provider pointer

    t0 = time.perf_counter()
    deadline = t0 + float(max(0.05, seconds))

    jn = len(jobs)
    j = 0

    while True:
        job = jobs[j]

        # scan providers until find eligible or give up after one full pass
        scanned = 0
        while scanned < nprov:
            p = providers[idx]
            idx += 1
            if idx == nprov:
                idx = 0
            scanned += 1
            if _eligible(p, job):
                p.avail -= 1
                assigned_total += 1
                break
        scans += scanned

        # next job
        j += 1
        if j == jn:
            j = 0
            # reset capacities for next cycle
            _reset_avail(providers)

        # pacing
        if (assigned_total & 0x3FF) == 0x3FF:  # periodic time check to reduce overhead
            if time.perf_counter() >= deadline:
                break

    t1 = time.perf_counter()
    elapsed = max(1e-9, t1 - t0)
    aps = assigned_total / elapsed

    # Quick utilization estimate: avg fraction of capacity consumed per cycle
    cap = sum(p.max_concurrent for p in providers)
    util = min(1.0, (assigned_total % max(1, cap)) / max(1.0, float(cap)))

    return {
        "assignments": assigned_total,
        "elapsed_s": elapsed,
        "assignments_per_s": aps,
        "utilization": util,
        "providers": nprov,
        "jobs_unique": jn,
    }


# -----------------------------------------------------------------------------
# SLA evaluation benchmark
# -----------------------------------------------------------------------------


@dataclass
class JobMetrics:
    traps_ratio: float  # 0..1
    qos: float  # 0..1
    latency_s: float
    availability: float  # 0..1


@dataclass
class SlaThresholds:
    traps_min: float = 0.60
    qos_min: float = 0.70
    latency_target_s: float = 2.0  # pass if latency <= 2*target
    availability_min: float = 0.985


def gen_metrics(n: int, seed: int) -> List[JobMetrics]:
    x = (seed ^ 0x5A5AA5A55A5AA5A5) & ((1 << 64) - 1) or 1
    out: List[JobMetrics] = []
    for _ in range(n):
        x, traps = _rand_float01(x)
        x, qos = _rand_float01(x)
        x, latj = _rand_float01(x)
        x, avail = _rand_float01(x)
        # latency in [0.1, 5.1) seconds, concentrated around ~2s
        latency = 0.1 + 5.0 * (latj * latj)
        out.append(
            JobMetrics(
                traps_ratio=traps,
                qos=qos,
                latency_s=latency,
                availability=avail * 0.02 + 0.98,
            )
        )
    return out


def sla_pass(m: JobMetrics, th: SlaThresholds) -> bool:
    if m.traps_ratio < th.traps_min:
        return False
    if m.qos < th.qos_min:
        return False
    if m.latency_s > (2.0 * th.latency_target_s):
        return False
    if m.availability < th.availability_min:
        return False
    return True


def run_sla_bench(
    seconds: float,
    dataset: List[JobMetrics],
    thresholds: SlaThresholds,
) -> dict:
    n = len(dataset)
    if n == 0:
        return {
            "evaluations": 0,
            "elapsed_s": 0.0,
            "evaluations_per_s": 0.0,
            "pass_rate": 0.0,
        }

    total = 0
    passed = 0
    idx = 0

    t0 = time.perf_counter()
    deadline = t0 + float(max(0.05, seconds))

    while True:
        m = dataset[idx]
        if sla_pass(m, thresholds):
            passed += 1
        total += 1

        idx += 1
        if idx == n:
            idx = 0

        if (total & 0x3FF) == 0x3FF:
            if time.perf_counter() >= deadline:
                break

    t1 = time.perf_counter()
    elapsed = max(1e-9, t1 - t0)
    eps = total / elapsed
    pass_rate = passed / float(total) if total else 0.0

    return {
        "evaluations": total,
        "elapsed_s": elapsed,
        "evaluations_per_s": eps,
        "pass_rate": pass_rate,
        "dataset": n,
        "thresholds": {
            "traps_min": thresholds.traps_min,
            "qos_min": thresholds.qos_min,
            "latency_target_s": thresholds.latency_target_s,
            "availability_min": thresholds.availability_min,
        },
    }


# -----------------------------------------------------------------------------
# Glue / CLI
# -----------------------------------------------------------------------------


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(
        description="AICF matcher & SLA evaluation throughput benchmarks."
    )
    ap.add_argument(
        "--providers", type=int, default=500, help="Number of providers (default: 500)"
    )
    ap.add_argument(
        "--jobs", type=int, default=10_000, help="Number of queued jobs (default: 10k)"
    )
    ap.add_argument(
        "--quota",
        type=int,
        default=8,
        help="Per-provider max concurrent leases (default: 8)",
    )
    ap.add_argument(
        "--match-seconds",
        type=float,
        default=2.5,
        help="Target seconds for matching bench (default: 2.5)",
    )
    ap.add_argument(
        "--sla-seconds",
        type=float,
        default=2.5,
        help="Target seconds for SLA bench (default: 2.5)",
    )
    ap.add_argument(
        "--seed",
        type=int,
        default=None,
        help="Deterministic seed (default: from PYTHONHASHSEED or 1337)",
    )
    ap.add_argument(
        "--traps-min",
        type=float,
        default=0.60,
        help="SLA minimum traps ratio (default: 0.60)",
    )
    ap.add_argument(
        "--qos-min", type=float, default=0.70, help="SLA minimum QoS (default: 0.70)"
    )
    ap.add_argument(
        "--latency-target",
        type=float,
        default=2.0,
        help="SLA latency target seconds (default: 2.0)",
    )
    ap.add_argument(
        "--availability-min",
        type=float,
        default=0.985,
        help="SLA minimum availability (default: 0.985)",
    )
    args = ap.parse_args(argv)

    seed = (
        args.seed
        if (args.seed is not None)
        else int(os.environ.get("PYTHONHASHSEED", "0") or "1337")
    )

    # Generate fixtures
    providers = gen_providers(args.providers, args.quota, seed)
    jobs = gen_jobs(args.jobs, seed)
    metrics = gen_metrics(max(10_000, args.jobs // 2), seed ^ 0xDEADBEEFCAFEBABE)

    # Run benches
    match_res = run_match_bench(args.match_seconds, providers, jobs)
    sla_th = SlaThresholds(
        traps_min=args.traps_min,
        qos_min=args.qos_min,
        latency_target_s=args.latency_target,
        availability_min=args.availability_min,
    )
    sla_res = run_sla_bench(args.sla_seconds, metrics, sla_th)

    payload = {
        "case": "aicf.matcher_and_sla",
        "params": {
            "providers": args.providers,
            "jobs": args.jobs,
            "quota": args.quota,
            "seed": seed,
            "match_seconds": args.match_seconds,
            "sla_seconds": args.sla_seconds,
        },
        "result": {
            "match": match_res,
            "sla": sla_res,
        },
    }
    print(json.dumps(payload, separators=(",", ":"), sort_keys=False))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
