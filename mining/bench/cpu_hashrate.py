"""
cpu_hashrate.py
===============

Micro-benchmark for the CPU inner loop used to search HashShare proofs.

What it measures
----------------
- Raw hashing throughput (hashes/sec) using SHA3-256 over (header_prefix || nonce32).
- For several Θ (nats), it estimates the *expected* share rate p = exp(-Θ), and
  also *observes* how many shares we actually find by checking H(u) = -ln(u) ≥ Θ,
  where u is derived from the 256-bit hash digest interpreted as a uniform value.

Notes
-----
- This is single-threaded on purpose to avoid GIL complications. It is intended
  to be a *relative* micro-bench across Θ values and environments.
- It does not depend on the full mining loop; it isolates the critical path.
- If you want to mirror the exact header hashing domain as in mining/hash_search.py,
  you can pass `header_prefix` from a real template; otherwise a synthetic one is used.

Usage
-----
    python -m mining.bench.cpu_hashrate
    # or programmatically:
    from mining.bench.cpu_hashrate import run
    stats = run(seconds=3.0, thetas=[2,4,6,8,10,12])

Return value (run)
------------------
A dict with keys:
- seconds, total_hashes, hashes_per_sec, seed
- results: list of {theta, p_exp, observed, expected, shares_per_sec, expected_per_sec}
- env: {python, platform}
"""

from __future__ import annotations

import hashlib
import math
import os
import struct
import sys
import time
from dataclasses import dataclass
from typing import Iterable, List, Optional

# Try to use core.utils.hash.sha3_256 if available, otherwise fall back to hashlib
try:
    from core.utils.hash import sha3_256 as _sha3_256  # type: ignore
except Exception:

    def _sha3_256(data: bytes) -> bytes:
        return hashlib.sha3_256(data).digest()


@dataclass
class BenchConfig:
    seconds: float = 3.0
    max_hashes: int = 50_000_000  # hard cap for very fast machines
    seed: Optional[int] = None
    # Θ values in natural log (nats). p(share) = exp(-Θ).
    thetas: List[float] = None  # type: ignore
    header_prefix: Optional[bytes] = None  # bytes to be prefixed before nonce

    def __post_init__(self):
        if self.thetas is None:
            # Choose a spread that gives both frequent and rare shares within a short run.
            self.thetas = [2.0, 3.0, 4.0, 6.0, 8.0, 10.0, 12.0]
        if self.header_prefix is None:
            # 64 bytes of deterministic-ish header material
            s = self.seed if self.seed is not None else 0xA1B2C3D4
            self.header_prefix = (
                b"ANIMICA-BENCH-HEADER\x00" + s.to_bytes(8, "big") + b"\x00" * 32
            )


@dataclass
class ThetaStats:
    theta: float
    p_exp: float
    observed: int
    expected: float

    @property
    def shares_per_sec(self) -> float:
        return 0.0  # filled by caller

    @property
    def expected_per_sec(self) -> float:
        return 0.0  # filled by caller


def _digest_to_u(d: bytes) -> float:
    """
    Map a 256-bit digest to a uniform u in (0,1], using:
        u = (x + 1) / 2^256
    with x = big-endian integer value of the digest.
    """
    x = int.from_bytes(d, "big")
    return (x + 1) * (2.0**-256)  # float is fine for thresholding at modest Θ


def _run_inner(cfg: BenchConfig) -> dict:
    # Prepare nonce and prefix
    nonce = cfg.seed if cfg.seed is not None else 0x1234_5678
    prefix = cfg.header_prefix
    assert prefix is not None
    pack_u32 = struct.Struct(">I").pack  # big-endian 32-bit nonce

    # Precompute expectations
    theta_meta = [
        {
            "theta": th,
            "p_exp": math.exp(-th),
            "observed": 0,
            "expected": 0.0,  # filled after we know total hashes
        }
        for th in cfg.thetas
    ]

    # Main loop
    t0 = time.perf_counter()
    total = 0
    deadline = t0 + max(0.01, cfg.seconds)

    # Tight loop: compute digest of prefix||nonce32, convert to u, test all thetas
    # Use locals for speed
    sha3_256 = _sha3_256
    thetas = [m["theta"] for m in theta_meta]
    logs = thetas  # we threshold on -ln(u) >= theta; equivalently u <= exp(-theta)
    thresholds = [math.exp(-th) for th in logs]  # u <= thresh triggers a share
    observed = [0] * len(thresholds)

    while total < cfg.max_hashes:
        # Batch a small fixed number per loop to amortize time checks
        for _ in range(2048):
            d = sha3_256(prefix + pack_u32(nonce))
            nonce = (nonce + 1) & 0xFFFF_FFFF
            # Convert to u
            u = _digest_to_u(d)
            # Compare against all thresholds
            # Cheap cascade: smallest theta (largest threshold) most likely hit
            for i, thr in enumerate(thresholds):
                if u <= thr:
                    observed[i] += 1
            total += 1
            if total >= cfg.max_hashes:
                break
        if time.perf_counter() >= deadline:
            break

    t1 = time.perf_counter()
    dt = max(1e-9, t1 - t0)
    hps = total / dt

    # Fill expectations & results
    results = []
    for i, m in enumerate(theta_meta):
        p = m["p_exp"]
        exp = total * p
        obs = observed[i]
        results.append(
            {
                "theta": m["theta"],
                "p_exp": p,
                "observed": int(obs),
                "expected": float(exp),
                "shares_per_sec": obs / dt,
                "expected_per_sec": hps * p,
            }
        )

    return {
        "seconds": dt,
        "total_hashes": total,
        "hashes_per_sec": hps,
        "seed": cfg.seed,
        "results": results,
        "env": {
            "python": sys.version.split()[0],
            "platform": sys.platform,
        },
    }


def run(
    seconds: float = 3.0,
    thetas: Optional[Iterable[float]] = None,
    seed: Optional[int] = None,
    header_prefix: Optional[bytes] = None,
    max_hashes: int = 50_000_000,
) -> dict:
    """
    Run the micro-benchmark.

    Args:
        seconds: target duration (best-effort).
        thetas: iterable of Θ (nats) thresholds to evaluate.
        seed: optional deterministic seed for nonce/header_prefix.
        header_prefix: optional header bytes to prefix before the nonce.
        max_hashes: hard cap to avoid very long runs on fast machines.

    Returns:
        Dict with throughput and per-Θ observed vs expected shares/sec.
    """
    cfg = BenchConfig(
        seconds=seconds,
        seed=seed,
        header_prefix=header_prefix,
        max_hashes=max_hashes,
        thetas=list(thetas) if thetas is not None else None,
    )
    return _run_inner(cfg)


def _fmt_rate(x: float) -> str:
    if x >= 1e9:
        return f"{x/1e9:.2f} G/s"
    if x >= 1e6:
        return f"{x/1e6:.2f} M/s"
    if x >= 1e3:
        return f"{x/1e3:.2f} k/s"
    return f"{x:.2f} /s"


def _main(argv: List[str]) -> int:
    # Tiny CLI: ANIMICA_BENCH_SECONDS, ANIMICA_BENCH_SEED, ANIMICA_BENCH_THETAS envs
    seconds = float(os.getenv("ANIMICA_BENCH_SECONDS", "3.0"))
    seed_env = os.getenv("ANIMICA_BENCH_SEED")
    seed = int(seed_env, 0) if seed_env else None
    thetas_env = os.getenv("ANIMICA_BENCH_THETAS")
    thetas = None
    if thetas_env:
        try:
            thetas = [float(t.strip()) for t in thetas_env.split(",") if t.strip()]
        except Exception:
            print(
                "Invalid ANIMICA_BENCH_THETAS; expected comma-separated floats (nats).",
                file=sys.stderr,
            )
            return 2

    out = run(seconds=seconds, thetas=thetas, seed=seed)
    hps = out["hashes_per_sec"]
    print(f"Animica CPU Hash micro-bench")
    print(
        f"  duration: {out['seconds']:.3f}s   total hashes: {out['total_hashes']:,}   throughput: {_fmt_rate(hps)}"
    )
    print(f"  python: {out['env']['python']}   platform: {out['env']['platform']}")
    print()
    print(
        f"{'Θ (nats)':>8}  {'p(exp)':>10}  {'obs':>10}  {'exp':>10}  {'obs/s':>12}  {'exp/s':>12}"
    )
    for r in out["results"]:
        print(
            f"{r['theta']:8.2f}  "
            f"{r['p_exp']:10.3e}  "
            f"{r['observed']:10d}  "
            f"{r['expected']:10.2f}  "
            f"{r['shares_per_sec']:12.3e}  "
            f"{r['expected_per_sec']:12.3e}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(_main(sys.argv))
