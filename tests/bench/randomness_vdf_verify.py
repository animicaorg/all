# -*- coding: utf-8 -*-
"""
randomness_vdf_verify.py
========================

Benchmark VDF verification throughput (verifies/sec).

If a real verifier is available at `randomness.vdf.verifier`, this script can be
extended to use it. For portability (and to avoid heavy proof generation), we
default to a *synthetic but faithful* verifier workload that mirrors the rough
cost profile of Wesolowski verification:

    check:   lhs = (pi^L * x^r) mod N   ?=   y

Where:
  - N is a large 2048-bit modulus (we use a fixed composite; primality isn't
    important for perf).
  - L is a ~255-bit exponent (standing in for a large prime challenge).
  - r is in [0, L).
  - (x, pi, y) are 2048-bit group elements. We *precompute* y using the same
    formula so verification passes deterministically.
  - Verification cost ~ two modular exponentiations + one multiply.

The script runs for a target duration and prints a single JSON object on stdout,
consumed by tests/bench/runner.py.

Examples:
    python tests/bench/randomness_vdf_verify.py
    python tests/bench/randomness_vdf_verify.py --seconds 3.5 --mod-bits 2048 --exp-bits 255
    python tests/bench/randomness_vdf_verify.py --batch 2000 --seed 1337
"""
from __future__ import annotations

import argparse
import json
import os
import time
from dataclasses import dataclass
from typing import List, Optional, Tuple

# -----------------------------------------------------------------------------
# Deterministic PRNG (tiny LCG) for reproducible datasets
# -----------------------------------------------------------------------------


def _lcg_next(x: int) -> int:
    # 64-bit LCG parameters
    a = 6364136223846793005
    c = 1442695040888963407
    return (a * x + c) & ((1 << 64) - 1)


def _u256_stream(seed: int) -> int:
    """
    Expand a 64-bit seed into a 256-bit-ish stream by concatenating LCG outputs.
    """
    x = seed or 1
    out = 0
    for _ in range(4):
        x = _lcg_next(x)
        out = (out << 64) | x
    return out


# -----------------------------------------------------------------------------
# Synthetic instance & verifier
# -----------------------------------------------------------------------------


@dataclass
class VdfInstance:
    x: int
    pi: int
    y: int
    r: int


def _make_modulus(mod_bits: int) -> int:
    """
    Use a fixed composite modulus 2^k - 159 (fast to construct; fine for perf).
    """
    if mod_bits < 512:
        mod_bits = 512
    return (1 << mod_bits) - 159


def _make_exponent_L(exp_bits: int) -> int:
    """
    Use a large odd number ~2^exp_bits; pick 2^b - 19 (Ed25519-like magnitude).
    """
    if exp_bits < 64:
        exp_bits = 64
    return (1 << exp_bits) - 19


def _mk_dataset(n: int, seed: int, N: int, L: int) -> List[VdfInstance]:
    """
    Build a dataset of n instances (x, pi, y, r) with y constructed so
    verification succeeds. Deterministic from seed.
    """
    s = (seed & ((1 << 64) - 1)) or 1
    out: List[VdfInstance] = []
    for _ in range(n):
        # Pseudo-random 2048-bit-ish values reduced mod N
        x_raw = _u256_stream(s)
        s = _lcg_next(s)
        pi_raw = _u256_stream(s)
        s = _lcg_next(s)
        r_raw = _u256_stream(s)
        s = _lcg_next(s)

        x = (x_raw % (N - 3)) + 2
        pi = (pi_raw % (N - 3)) + 2
        r = int(r_raw % L)

        # y = pi^L * x^r mod N  (so verify passes)
        y = (pow(pi, L, N) * pow(x, r, N)) % N

        out.append(VdfInstance(x=x, pi=pi, y=y, r=r))
    return out


def _verify_instance(inst: VdfInstance, N: int, L: int) -> bool:
    """
    Synthetic verifier mirroring Wesolowski check cost: two powmods + multiply.
    """
    lhs = (pow(inst.pi, L, N) * pow(inst.x, inst.r, N)) % N
    return lhs == inst.y


# -----------------------------------------------------------------------------
# Bench runner
# -----------------------------------------------------------------------------


def run_bench(
    seconds: float,
    batch: int,
    seed: Optional[int],
    mod_bits: int,
    exp_bits: int,
) -> dict:
    """
    Run verification batches until `seconds` have elapsed. Returns a JSON-serializable dict.
    """
    N = _make_modulus(mod_bits)
    L = _make_exponent_L(exp_bits)
    s = (
        seed
        if (seed is not None)
        else int(os.environ.get("PYTHONHASHSEED", "0") or "1337")
    )

    # Pre-build dataset outside timing window
    dataset = _mk_dataset(batch, s, N, L)

    total_verifies = 0
    total_ok = 0
    t0 = time.perf_counter()
    deadline = t0 + float(max(0.05, seconds))

    # Loop over dataset repeatedly until time's up
    idx = 0
    n = len(dataset)
    while True:
        inst = dataset[idx]
        if _verify_instance(inst, N, L):
            total_ok += 1
        total_verifies += 1

        idx += 1
        if idx == n:
            idx = 0

        if (total_verifies % 64) == 0:
            if time.perf_counter() >= deadline:
                break

    t1 = time.perf_counter()
    elapsed = max(1e-9, t1 - t0)
    vps = total_verifies / elapsed

    return {
        "case": "randomness.vdf_verify",
        "params": {
            "seconds": seconds,
            "batch": batch,
            "seed": s,
            "mod_bits": mod_bits,
            "exp_bits": exp_bits,
            "mode": "synthetic-wesolowski-like",
        },
        "result": {
            "verifies": total_verifies,
            "ok": total_ok,
            "elapsed_s": elapsed,
            "verifies_per_s": vps,
        },
    }


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(
        description="Benchmark VDF verification throughput (verifies/sec)."
    )
    ap.add_argument(
        "--seconds",
        type=float,
        default=2.5,
        help="Target runtime in seconds (default: 2.5)",
    )
    ap.add_argument(
        "--batch",
        type=int,
        default=1000,
        help="Dataset size, reused in loop (default: 1000)",
    )
    ap.add_argument(
        "--seed",
        type=int,
        default=None,
        help="Deterministic dataset seed (default: from PYTHONHASHSEED or 1337)",
    )
    ap.add_argument(
        "--mod-bits",
        type=int,
        default=2048,
        help="Modulus size in bits (default: 2048)",
    )
    ap.add_argument(
        "--exp-bits",
        type=int,
        default=255,
        help="Exponent L size in bits (default: 255)",
    )
    args = ap.parse_args(argv)

    payload = run_bench(
        seconds=args.seconds,
        batch=args.batch,
        seed=args.seed,
        mod_bits=args.mod_bits,
        exp_bits=args.exp_bits,
    )
    print(json.dumps(payload, separators=(",", ":"), sort_keys=False))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
