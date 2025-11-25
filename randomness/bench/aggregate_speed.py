#!/usr/bin/env python3
"""
Commit/Reveal aggregation throughput benchmark.

Measures the performance of the bias-resistant aggregation combiner used to
fold many reveal payloads into a single round hash.

Assumptions about the implementation (adapt if needed):
  - randomness.commit_reveal.aggregate exports one of:
        * aggregate(reveals: list[bytes]) -> bytes
        * aggregate_reveals(reveals: list[bytes]) -> bytes
  - Each "reveal" is represented here by a bytes payload (e.g., 32B).
    If your implementation expects a richer type, adapt the synthesizer
    accordingly to construct the right objects.

What this measures:
  - End-to-end wall time per aggregation over N reveals
  - Aggregations/sec, reveals/sec, and effective MB/sec
  - Supports multiple participant counts and multiple rounds per point

Examples:
  python -m randomness.bench.aggregate_speed \
      --participants 1k,10k,100k \
      --payload-bytes 32 \
      --rounds 10 \
      --missing 0.0 \
      --csv agg.csv

  python -m randomness.bench.aggregate_speed --participants 50k --rounds 20 --seed 0xBEEF
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import statistics as stats
import sys
import time
from dataclasses import dataclass, asdict
from hashlib import sha3_256
from typing import Callable, Iterable, List

# ---- Import aggregator -------------------------------------------------------
_agg_err = None
agg_fn: Callable[[List[bytes]], bytes]

try:
    from randomness.commit_reveal.aggregate import aggregate as _aggregate  # type: ignore
    agg_fn = _aggregate
except Exception as e1:
    try:
        from randomness.commit_reveal.aggregate import aggregate_reveals as _aggregate_reveals  # type: ignore
        agg_fn = _aggregate_reveals
    except Exception as e2:
        _agg_err = (e1, e2)
        agg_fn = None  # type: ignore


# ---- Helpers ----------------------------------------------------------------
def _parse_num_list(s: str) -> list[int]:
    """
    Parse a comma-separated list of integers with optional suffixes:
      k/K (1e3), m/M (1e6)
      scientific notation like 2e6 also accepted
    """
    out: list[int] = []
    for part in s.split(","):
        t = part.strip().lower()
        if not t:
            continue
        mul = 1
        if t.endswith(("k", "m")):
            mul = 1_000 if t[-1] == "k" else 1_000_000
            t = t[:-1]
        if "e" in t:
            v = float(t)
            out.append(int(v))
        else:
            out.append(int(float(t) * mul))
    return out


def _seeded_bytes(seed_int: int, *tags: int, length: int = 32) -> bytes:
    b = seed_int.to_bytes((seed_int.bit_length() + 7) // 8 or 1, "big")
    for t in tags:
        b += t.to_bytes((t.bit_length() + 7) // 8 or 1, "big") + b"|"
    return sha3_256(b).digest()[:length]


def _synthesize_reveals(n: int, payload_len: int, seed: int, missing_frac: float, round_no: int) -> list[bytes]:
    """
    Build a deterministic list of payload bytes. Optionally drop a fraction
    to simulate missing reveals (they simply won't be included).
    """
    reveals: list[bytes] = []
    drop_every = int(1.0 / missing_frac) if 0.0 < missing_frac < 1.0 else 0
    for i in range(n):
        if drop_every and (i % drop_every == 0):
            # simulate a missing reveal
            continue
        reveals.append(_seeded_bytes(seed, round_no, i, length=payload_len))
    return reveals


@dataclass
class BenchPoint:
    participants: int
    payload_bytes: int
    rounds: int
    missing_frac: float
    agg_mean_ms: float
    agg_median_ms: float
    agg_p95_ms: float
    aggs_per_sec: float
    reveals_per_sec: float
    mb_per_sec: float

    def to_row(self) -> list[str | int | float]:
        return [
            self.participants,
            self.payload_bytes,
            self.rounds,
            f"{self.missing_frac:.3f}",
            f"{self.agg_mean_ms:.3f}",
            f"{self.agg_median_ms:.3f}",
            f"{self.agg_p95_ms:.3f}",
            f"{self.aggs_per_sec:.2f}",
            f"{self.reveals_per_sec:.2f}",
            f"{self.mb_per_sec:.2f}",
        ]


def _fmt_table(points: list[BenchPoint]) -> str:
    headers = [
        "participants",
        "payload_B",
        "rounds",
        "missing",
        "mean_ms",
        "median_ms",
        "p95_ms",
        "aggs/s",
        "reveals/s",
        "MB/s",
    ]
    rows = [p.to_row() for p in points]
    widths = [len(h) for h in headers]
    for r in rows:
        for i, cell in enumerate(r):
            widths[i] = max(widths[i], len(str(cell)))
    def fmt_row(r: list[object]) -> str:
        return "  ".join(str(cell).rjust(widths[i]) for i, cell in enumerate(r))
    out = [fmt_row(headers), "  ".join("-" * w for w in widths)]
    out.extend(fmt_row(r) for r in rows)
    return "\n".join(out)


# ---- Benchmark core ----------------------------------------------------------
def bench_one(participants: int, payload_bytes: int, rounds: int, seed: int, missing: float) -> BenchPoint:
    if agg_fn is None:  # pragma: no cover
        e1, e2 = _agg_err if _agg_err else (None, None)
        msg = (
            "ERROR: Could not import aggregation function from randomness.commit_reveal.aggregate.\n"
            f" Tried aggregate (error: {e1}) and aggregate_reveals (error: {e2})."
        )
        print(msg, file=sys.stderr)
        sys.exit(2)

    latencies_ms: list[float] = []
    total_reveals = 0
    total_bytes = 0

    # Pre-synthesize per round to avoid timing generation
    synthesized = [
        _synthesize_reveals(participants, payload_bytes, seed=seed, missing_frac=missing, round_no=r)
        for r in range(rounds)
    ]

    for r in range(rounds):
        reveals = synthesized[r]
        total_reveals += len(reveals)
        total_bytes += len(reveals) * payload_bytes

        t0 = time.perf_counter()
        _ = agg_fn(reveals)  # result unused; we only time the combiner
        dt_ms = (time.perf_counter() - t0) * 1_000.0
        latencies_ms.append(dt_ms)

    mean_ms = stats.fmean(latencies_ms)
    median_ms = stats.median(latencies_ms)
    p95_ms = stats.quantiles(latencies_ms, n=20)[18] if len(latencies_ms) >= 20 else max(latencies_ms)

    aggs_per_sec = 1000.0 / mean_ms if mean_ms > 0 else float("inf")
    reveals_per_sec = (total_reveals / rounds) * aggs_per_sec
    mb_per_sec = ((total_bytes / rounds) / (1024 * 1024)) * aggs_per_sec

    return BenchPoint(
        participants=participants,
        payload_bytes=payload_bytes,
        rounds=rounds,
        missing_frac=missing,
        agg_mean_ms=mean_ms,
        agg_median_ms=median_ms,
        agg_p95_ms=p95_ms,
        aggs_per_sec=aggs_per_sec,
        reveals_per_sec=reveals_per_sec,
        mb_per_sec=mb_per_sec,
    )


# ---- CLI --------------------------------------------------------------------
def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Aggregate many reveals and measure throughput.")
    ap.add_argument("--participants", type=str, default="10k", help="Comma-separated counts, supports k/m suffixes.")
    ap.add_argument("--payload-bytes", type=int, default=32, help="Reveal payload size in bytes (default 32).")
    ap.add_argument("--rounds", type=int, default=10, help="Repetitions per participants point.")
    ap.add_argument("--missing", type=float, default=0.0, help="Fraction [0,1) of missing reveals to simulate.")
    ap.add_argument("--seed", type=lambda x: int(x, 0), default=0xA61CE, help="Deterministic seed (int, 0x… ok).")
    ap.add_argument("--csv", type=str, default="", help="Optional path to write CSV results.")
    ap.add_argument("--json", type=str, default="", help="Optional path to write JSON results.")
    args = ap.parse_args(argv)

    parts_list = _parse_num_list(args.participants)

    points: list[BenchPoint] = []
    for n in parts_list:
        points.append(bench_one(n, args.payload_bytes, args.rounds, args.seed, args.missing))

    print(_fmt_table(points))

    if args.csv:
        with open(args.csv, "w", newline="") as f:
            w = csv.writer(f)
            w.writerow([
                "participants","payload_bytes","rounds","missing_frac",
                "mean_ms","median_ms","p95_ms","aggs_per_sec","reveals_per_sec","mb_per_sec"
            ])
            for p in points:
                w.writerow([
                    p.participants, p.payload_bytes, p.rounds, f"{p.missing_frac:.6f}",
                    f"{p.agg_mean_ms:.6f}", f"{p.agg_median_ms:.6f}", f"{p.agg_p95_ms:.6f}",
                    f"{p.aggs_per_sec:.6f}", f"{p.reveals_per_sec:.6f}", f"{p.mb_per_sec:.6f}",
                ])
        print(f"\nWrote CSV: {args.csv}")

    if args.json:
        with open(args.json, "w") as f:
            json.dump([asdict(p) for p in points], f, indent=2)
        print(f"Wrote JSON: {args.json}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
