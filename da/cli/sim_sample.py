from __future__ import annotations

"""
Animica • DA • sim_sample
=========================

Simulate Data-Availability Sampling (DAS) detection probability for a Reed–Solomon
RS(n, k) layout. Reports p_fail = probability that *sampling fails to detect*
an unavailable blob (i.e., all samples land on available shares even though the
blob is unrecoverable).

Two models:
- "hyper" (default): exact hypergeometric without replacement
    p_fail = C(n - m, s) / C(n, s)
- "approx": with-replacement approximation
    p_fail ≈ ((n - m) / n) ** s

Where:
- n = total shares
- k = data (information) shares
- m = number of missing/corrupted shares (defaults to n - k + 1, the minimal
      unrecoverable case for RS(n, k))

You can also provide m explicitly or as a fraction of n via --unavailable-frac.

Optionally, solve for the number of samples `s` needed to reach a target p_fail
with --target-pfail. In "hyper" mode this is computed via a small binary search.

Examples
--------
# Minimal: worst-case m = n - k + 1
python -m da.cli.sim_sample --n 512 --k 256 --samples 80

# Specify an adversarial fraction (25% missing), exact hypergeometric
python -m da.cli.sim_sample --n 512 --k 256 --samples 60 --unavailable-frac 0.25

# Ask for required samples to hit p_fail <= 1e-9 (keeps other args)
python -m da.cli.sim_sample --n 512 --k 256 --target-pfail 1e-9

# Approximate model (with replacement)
python -m da.cli.sim_sample --n 512 --k 256 --samples 80 --mode approx

Notes
-----
This tool is analytical and does not contact a DA service. Use it to size
sampling policies or to reason about parameters visible in headers.
"""

import argparse
import json
import math
from typing import Optional, Tuple


def _ceil_int(x: float) -> int:
    return int(math.ceil(x))


def _comb_log(n: int, k: int) -> float:
    """log(C(n,k)) using lgamma for numeric stability."""
    if k < 0 or k > n:
        return float("-inf")
    return math.lgamma(n + 1) - math.lgamma(k + 1) - math.lgamma(n - k + 1)


def p_fail_hyper(n: int, m: int, s: int) -> float:
    """
    Exact hypergeometric (sampling w/o replacement):
        p_fail = C(n - m, s) / C(n, s)
    Edge cases:
      - if s > n: clamp to n
      - if s > n - m: p_fail = 0 (you must hit a missing share)
      - if m <= 0: p_fail = 1 (blob is available; cannot detect unavailability)
    """
    if s <= 0:
        return 1.0
    s = min(s, n)
    if m <= 0:
        return 1.0
    if m > n:
        m = n
    if s > n - m:
        return 0.0
    log_num = _comb_log(n - m, s)
    log_den = _comb_log(n, s)
    x = math.exp(log_num - log_den)
    # Numerical hygiene
    return max(0.0, min(1.0, x))


def p_fail_approx(n: int, m: int, s: int) -> float:
    """
    With-replacement approximation:
        p_fail ≈ ((n - m) / n) ** s
    """
    if s <= 0:
        return 1.0
    if m <= 0:
        return 1.0
    if m >= n:
        return 0.0
    good = (n - m) / n
    return max(0.0, min(1.0, good ** s))


def solve_samples_for_target(
    n: int,
    m: int,
    target_pfail: float,
    mode: str = "hyper",
    max_s: Optional[int] = None,
) -> int:
    """
    Find the smallest s such that p_fail(s) <= target_pfail.

    For 'approx' mode we solve analytically:
        s >= ln(target) / ln((n - m)/n)

    For 'hyper' we binary-search s in [0, max_s], where max_s defaults to n.
    """
    target = float(target_pfail)
    target = max(0.0, min(1.0, target))

    if m <= 0:
        return 0  # blob is fully available; p_fail = 1 always, but "detecting unavailability" is moot.

    if mode == "approx":
        good = (n - m) / n
        if good <= 0.0:
            return 1
        if target <= 0.0:
            return (max_s or n)
        if target >= 1.0:
            return 0
        s = math.log(target) / math.log(good)
        return _ceil_int(max(0.0, s))

    # hyper: binary search
    hi = max_s if max_s is not None else n
    lo = 0
    best = hi
    while lo <= hi:
        mid = (lo + hi) // 2
        pf = p_fail_hyper(n, m, mid)
        if pf <= target:
            best = mid
            hi = mid - 1
        else:
            lo = mid + 1
    return best


def parse_args(argv: Optional[list[str]] = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Animica • DA • simulate DAS sampling failure probability"
    )
    p.add_argument("--n", type=int, required=True, help="total shares (n)")
    p.add_argument("--k", type=int, required=True, help="data shares (k)")
    group = p.add_mutually_exclusive_group()
    group.add_argument("--m", type=int, help="missing shares (m). Default: n - k + 1")
    group.add_argument(
        "--unavailable-frac",
        type=float,
        help="fraction of unavailable shares in [0,1]. Overrides --m if given.",
    )
    p.add_argument(
        "--samples",
        type=int,
        help="number of samples (s). If omitted, must provide --target-pfail",
    )
    p.add_argument(
        "--target-pfail",
        type=float,
        help="solve for minimal samples s so that p_fail <= target (e.g., 1e-9)",
    )
    p.add_argument(
        "--mode",
        choices=["hyper", "approx"],
        default="hyper",
        help="probability model (default: hyper = exact hypergeometric)",
    )
    p.add_argument(
        "--json",
        action="store_true",
        help="print machine-readable JSON",
    )
    p.add_argument(
        "--explain",
        action="store_true",
        help="print extra explanation lines in text mode",
    )
    return p.parse_args(argv)


def main(argv: Optional[list[str]] = None) -> int:
    args = parse_args(argv)

    n = int(args.n)
    k = int(args.k)
    if n <= 0 or k <= 0 or k > n:
        print("error: require n>0, 0<k<=n", flush=True)
        return 2

    # Determine m (missing) from inputs
    if args.unavailable_frac is not None:
        f = max(0.0, min(1.0, float(args.unavailable_frac)))
        m = int(math.ceil(f * n))
    elif args.m is not None:
        m = max(0, int(args.m))
    else:
        # Minimal unrecoverable case: just over the RS threshold
        m = max(1, n - k + 1)

    if m > n:
        m = n

    # Either compute p_fail for given s, or solve s for a target p_fail
    mode = args.mode
    pfail: Optional[float] = None
    samples: Optional[int] = args.samples

    if args.target_pfail is not None and samples is None:
        target = float(args.target_pfail)
        samples = solve_samples_for_target(n, m, target, mode=mode, max_s=n)
        # Also compute the actual p_fail at that s
        if mode == "hyper":
            pfail = p_fail_hyper(n, m, samples)
        else:
            pfail = p_fail_approx(n, m, samples)
    elif samples is not None:
        s = max(0, int(samples))
        if mode == "hyper":
            pfail = p_fail_hyper(n, m, s)
        else:
            pfail = p_fail_approx(n, m, s)
        samples = s
    else:
        print("error: must provide either --samples or --target-pfail", flush=True)
        return 2

    result = {
        "n": n,
        "k": k,
        "m": m,
        "mode": mode,
        "samples": samples,
        "p_fail": pfail,
        "p_detect": (None if pfail is None else 1.0 - pfail),
        "assumptions": {
            "definition": "p_fail is probability all samples hit available shares despite unrecoverable blob",
            "minimal_unrecoverable_m": max(1, n - k + 1),
        },
    }

    if args.json:
        print(json.dumps(result, indent=2))
        return 0

    # Human-readable
    print("Animica • DA • DAS Simulation")
    print(f"Layout     : RS(n={n}, k={k})")
    print(f"Missing m  : {m} ({m/n:.2%} of shares)")
    print(f"Mode       : {mode}")
    print(f"Samples s  : {samples}")
    print(f"p_fail     : {pfail:.3e}")
    print(f"p_detect   : {1.0 - pfail:.3e}")
    if args.explain:
        print()
        print("Notes:")
        print("- p_fail is the probability that sampling DOES NOT detect unavailability.")
        print("- For RS(n,k), any m >= n-k+1 makes the blob unrecoverable; we default to this minimal case.")
        print("- 'hyper' uses exact hypergeometric (no replacement); 'approx' assumes independent draws.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
