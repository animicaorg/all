#!/usr/bin/env python3
"""
VDF verify throughput benchmark (Wesolowski)

Measures verification time for the Wesolowski VDF across a grid of parameters:
- RSA modulus size (bits)
- Iteration count T

For each (bits, T) pair, this script:
  1) Deterministically derives an RSA-like modulus N via the wesolowski utilities.
  2) Builds a deterministic input x from --seed.
  3) Generates a proof (y, pi) ONCE via the reference prover.
  4) Runs the verifier multiple times (with warmups) and reports:
       - mean/median/95p latency (ms)
       - verifies/sec
       - success rate (should be 100%)

Usage examples:
  python -m randomness.bench.vdf_verify_time --bits 1024,2048 --iters 2e6,5e6 --reps 15 --warmup 3
  python -m randomness.bench.vdf_verify_time --bits 2048 --iters 10m --csv vdf_verify.csv

Notes:
  - Proof generation is done once per parameter point and excluded from timing.
  - This script expects:
        randomness.vdf.wesolowski:  dev_modulus(bits:int, seed:bytes)->int, prove(x:int, T:int, N:int)->tuple[int, bytes]
        randomness.vdf.verifier:    verify(x:int, y:int, pi:bytes, T:int, N:int)->bool
    If your function names differ, adapt the imports below accordingly.
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import statistics as stats
import sys
import time
from dataclasses import dataclass, asdict
from hashlib import sha3_256

# --- Attempt imports from project modules ------------------------------------
try:
    # Reference prover & helpers
    from randomness.vdf.wesolowski import dev_modulus, prove  # type: ignore
    # Verifier (consensus check)
    from randomness.vdf.verifier import verify  # type: ignore
except Exception as e:  # pragma: no cover - graceful message for missing deps
    print(
        "ERROR: Could not import VDF modules. "
        "Ensure 'randomness.vdf.wesolowski' and 'randomness.vdf.verifier' are available.\n"
        f"Import error: {e}",
        file=sys.stderr,
    )
    sys.exit(2)


# --- Helpers -----------------------------------------------------------------
def _parse_num_list(s: str) -> list[int]:
    """
    Parse a comma-separated list of integers with optional suffixes:
      - k / K => *1_000
      - m / M => *1_000_000
      - e.g. '500k,2e6,10M' are accepted
    """
    out: list[int] = []
    for part in s.split(","):
        t = part.strip().lower()
        if not t:
            continue
        mul = 1
        if t.endswith(("k", "m")):
            suffix = t[-1]
            t = t[:-1]
            if suffix == "k":
                mul = 1_000
            elif suffix == "m":
                mul = 1_000_000
        # scientific notation like 2e6
        if "e" in t:
            v = float(t)
            out.append(int(v))
        else:
            out.append(int(float(t) * mul))
    return out


def _sha3_int(seed: bytes, tag: bytes) -> int:
    h = sha3_256(seed + b"|" + tag).digest()
    return int.from_bytes(h, "big")


def _format_ms(ns: float) -> str:
    return f"{ns:.3f}"


@dataclass
class BenchPoint:
    bits: int
    T: int
    reps: int
    warmup: int
    ok: int
    fail: int
    mean_ms: float
    median_ms: float
    p95_ms: float
    vps: float  # verifies per second

    def to_row(self) -> list[str | int | float]:
        return [
            self.bits,
            self.T,
            self.reps,
            self.warmup,
            self.ok,
            self.fail,
            _format_ms(self.mean_ms),
            _format_ms(self.median_ms),
            _format_ms(self.p95_ms),
            f"{self.vps:.2f}",
        ]


# --- Core benchmarking --------------------------------------------------------
def bench_verify(bits: int, T: int, seed: int, reps: int, warmup: int) -> BenchPoint:
    seed_bytes = seed.to_bytes((seed.bit_length() + 7) // 8 or 1, "big")

    # Derive modulus deterministically from seed+bits (devnet helper).
    N = dev_modulus(bits=bits, seed=sha3_256(seed_bytes + b"|mod|" + str(bits).encode()).digest())

    # Deterministic base element x in QR_N (we'll rely on implementation details to map to a valid base)
    x = _sha3_int(seed_bytes, b"x") % N
    if x == 0:
        x = 2

    # Prove once (excluded from timing)
    y, pi = prove(x=x, T=T, N=N)

    # Warmups
    for _ in range(warmup):
        verify(x=x, y=y, pi=pi, T=T, N=N)

    # Timed reps
    times_ms: list[float] = []
    ok = 0
    fail = 0
    for _ in range(reps):
        t0 = time.perf_counter()
        valid = verify(x=x, y=y, pi=pi, T=T, N=N)
        dt = (time.perf_counter() - t0) * 1_000.0
        times_ms.append(dt)
        if valid:
            ok += 1
        else:
            fail += 1

    mean_ms = stats.fmean(times_ms)
    median_ms = stats.median(times_ms)
    p95_ms = stats.quantiles(times_ms, n=20)[18] if len(times_ms) >= 20 else max(times_ms)
    vps = 1000.0 / mean_ms if mean_ms > 0 else float("inf")

    return BenchPoint(
        bits=bits,
        T=T,
        reps=reps,
        warmup=warmup,
        ok=ok,
        fail=fail,
        mean_ms=mean_ms,
        median_ms=median_ms,
        p95_ms=p95_ms,
        vps=vps,
    )


def print_table(points: list[BenchPoint]) -> None:
    cols = ["bits", "T", "reps", "warmup", "ok", "fail", "mean_ms", "median_ms", "p95_ms", "verifies/sec"]
    rows = [p.to_row() for p in points]

    widths = [len(c) for c in cols]
    for r in rows:
        for i, cell in enumerate(r):
            widths[i] = max(widths[i], len(str(cell)))

    def _fmt_row(r: list[object]) -> str:
        return "  ".join(str(cell).rjust(widths[i]) for i, cell in enumerate(r))

    print(_fmt_row(cols))
    print("  ".join("-" * w for w in widths))
    for r in rows:
        print(_fmt_row(r))


# --- CLI ---------------------------------------------------------------------
def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Benchmark Wesolowski VDF verification throughput")
    ap.add_argument(
        "--bits",
        type=str,
        default="2048",
        help="Comma-separated modulus sizes in bits (e.g. '1024,2048')",
    )
    ap.add_argument(
        "--iters",
        type=str,
        default="5m",
        help="Comma-separated iteration counts T (supports k/m/e notation; e.g. '2e6,5m')",
    )
    ap.add_argument("--reps", type=int, default=15, help="Verification repetitions per point (timed)")
    ap.add_argument("--warmup", type=int, default=3, help="Warmup verifications per point (not timed)")
    ap.add_argument("--seed", type=lambda x: int(x, 0), default=0xA11CE, help="Deterministic seed (int, 0x… ok)")
    ap.add_argument("--csv", type=str, default="", help="Optional path to write CSV results")
    ap.add_argument("--json", type=str, default="", help="Optional path to write JSON results")
    args = ap.parse_args(argv)

    bits_list = [int(b.strip()) for b in args.bits.split(",") if b.strip()]
    iters_list = _parse_num_list(args.iters)

    points: list[BenchPoint] = []
    for bits in bits_list:
        for T in iters_list:
            p = bench_verify(bits=bits, T=T, seed=args.seed, reps=args.reps, warmup=args.warmup)
            points.append(p)

    print_table(points)

    if args.csv:
        with open(args.csv, "w", newline="") as f:
            w = csv.writer(f)
            w.writerow(["bits", "T", "reps", "warmup", "ok", "fail", "mean_ms", "median_ms", "p95_ms", "verifies_per_sec"])
            for p in points:
                w.writerow([p.bits, p.T, p.reps, p.warmup, p.ok, p.fail, f"{p.mean_ms:.6f}", f"{p.median_ms:.6f}", f"{p.p95_ms:.6f}", f"{p.vps:.6f}"])
        print(f"\nWrote CSV: {args.csv}")

    if args.json:
        with open(args.json, "w") as f:
            json.dump([asdict(p) for p in points], f, indent=2)
        print(f"Wrote JSON: {args.json}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
