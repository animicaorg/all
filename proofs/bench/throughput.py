#!/usr/bin/env python3
"""
Animica — proofs.bench.throughput

Micro-benchmark harness for proof verifiers. It times end-to-end verification
calls over the bundled test vectors and reports ops/sec and latency stats.

Supported kinds:
  - hash       → proofs.hashshare
  - ai         → proofs.ai
  - quantum    → proofs.quantum
  - storage    → proofs.storage
  - vdf        → proofs.vdf

Usage examples:
  python -m proofs.bench.throughput --kind hash --repeat 1000
  python -m proofs.bench.throughput --kind ai --limit 50 --repeat 200
  python -m proofs.bench.throughput --kind all --repeat 200

Notes:
- This runs single-threaded to keep results comparable and avoid GIL contention.
- It relies on the module-level verify functions; if unavailable, it falls back
  to the registry dispatcher when possible.
- Vectors are read from proofs/test_vectors/<kind>.json. If a kind has no
  vectors installed, the bench skips it with an informative message.
"""

from __future__ import annotations

import argparse
import json
import os
import statistics as stats
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Sequence

# Resolve repo root relative to this file for default vector paths
HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]  # ~/animica/proofs
VECTORS_DIR = REPO / "test_vectors"

# ---------- Utilities ----------


def pctl(values: Sequence[float], q: float) -> float:
    if not values:
        return float("nan")
    idx = max(0, min(len(values) - 1, int(round((q / 100.0) * (len(values) - 1)))))
    return sorted(values)[idx]


@dataclass
class BenchResult:
    kind: str
    samples: int
    repeats: int
    successes: int
    failures: int
    total_calls: int
    total_s: float
    latencies_s: list[float]

    def render(self) -> str:
        if self.total_calls == 0:
            return f"{self.kind:8s} | no calls"
        lat = self.latencies_s
        ops = self.total_calls / self.total_s if self.total_s > 0 else float("inf")
        lines = [
            f"{self.kind:8s} | calls={self.total_calls:,} ok={self.successes:,} fail={self.failures:,} total={self.total_s:.3f}s  → {ops:,.0f} ops/s",
            f"           | lat(ms): min={min(lat)*1e3:6.2f}  p50={stats.median(lat)*1e3:6.2f}  p90={pctl(lat,90)*1e3:6.2f}  p99={pctl(lat,99)*1e3:6.2f}  max={max(lat)*1e3:6.2f}",
        ]
        return "\n".join(lines)


# ---------- Loader for test vectors ----------


def load_vectors(kind: str, override: Path | None = None) -> list[dict[str, Any]]:
    """
    Expects files like:
      proofs/test_vectors/hashshare.json
      proofs/test_vectors/ai.json
      proofs/test_vectors/quantum.json
      proofs/test_vectors/storage.json
      proofs/test_vectors/vdf.json

    Format (per file) should contain a list under "valid" (and optionally "invalid").
    Each entry should be an envelope/context bundle acceptable by the corresponding
    verifier's verify(...) function, or a compact form that registry can handle.
    """
    filename = {
        "hash": "hashshare.json",
        "ai": "ai.json",
        "quantum": "quantum.json",
        "storage": "storage.json",
        "vdf": "vdf.json",
    }.get(kind)
    if not filename:
        raise ValueError(f"unknown kind {kind!r}")

    path = override if override is not None else (VECTORS_DIR / filename)
    if not path.exists():
        return []

    with open(path, "rb") as f:
        doc = json.load(f)

    valid = doc.get("valid") or doc.get("vectors") or []
    if isinstance(valid, dict):  # some files might group cases by labels
        # flatten dict-of-lists to a single list
        out: list[dict[str, Any]] = []
        for _k, arr in valid.items():
            if isinstance(arr, list):
                out.extend(arr)
        valid = out
    if not isinstance(valid, list):
        return []
    return valid


# ---------- Resolve verifier callables ----------


@dataclass
class Verifier:
    kind: str
    call: Callable[..., Any]
    adapter: Callable[[dict[str, Any]], tuple[tuple, dict]]


def _import_verify(kind: str) -> Verifier:
    """
    Resolve a verify function and a small adapter that maps a test-vector entry
    into (*args, **kwargs) for the call.

    We support both direct module verify() style and the registry fallback.
    """
    if kind == "hash":
        try:
            from proofs import hashshare as mod  # type: ignore

            verify_fn = getattr(mod, "verify")
        except Exception:
            # Fallback to registry
            from proofs import registry as reg  # type: ignore

            verify_fn = lambda envelope, **ctx: reg.verify_envelope(envelope, ctx)  # type: ignore

        def adapt(entry: dict[str, Any]) -> tuple[tuple, dict]:
            # Allow both {envelope, header} and already-encoded envelope
            env = entry.get("envelope") or entry.get("proof") or entry
            header = entry.get("header") or entry.get("context") or {}
            return (env,), {"header": header}

        return Verifier(kind="hash", call=verify_fn, adapter=adapt)

    if kind == "ai":
        try:
            from proofs import ai as mod  # type: ignore

            verify_fn = getattr(mod, "verify")
        except Exception:
            from proofs import registry as reg  # type: ignore

            verify_fn = lambda envelope, **ctx: reg.verify_envelope(envelope, ctx)  # type: ignore

        def adapt(entry: dict[str, Any]) -> tuple[tuple, dict]:
            env = entry.get("envelope") or entry.get("proof") or entry
            policy = entry.get("policy") or {}
            vendor_roots = entry.get("vendor_roots") or {}
            return (env,), {"policy": policy, "vendor_roots": vendor_roots}

        return Verifier(kind="ai", call=verify_fn, adapter=adapt)

    if kind == "quantum":
        try:
            from proofs import quantum as mod  # type: ignore

            verify_fn = getattr(mod, "verify")
        except Exception:
            from proofs import registry as reg  # type: ignore

            verify_fn = lambda envelope, **ctx: reg.verify_envelope(envelope, ctx)  # type: ignore

        def adapt(entry: dict[str, Any]) -> tuple[tuple, dict]:
            env = entry.get("envelope") or entry.get("proof") or entry
            policy = entry.get("policy") or {}
            vendor_roots = entry.get("vendor_roots") or {}
            return (env,), {"policy": policy, "vendor_roots": vendor_roots}

        return Verifier(kind="quantum", call=verify_fn, adapter=adapt)

    if kind == "storage":
        try:
            from proofs import storage as mod  # type: ignore

            verify_fn = getattr(mod, "verify")
        except Exception:
            from proofs import registry as reg  # type: ignore

            verify_fn = lambda envelope, **ctx: reg.verify_envelope(envelope, ctx)  # type: ignore

        def adapt(entry: dict[str, Any]) -> tuple[tuple, dict]:
            env = entry.get("envelope") or entry.get("proof") or entry
            policy = entry.get("policy") or {}
            return (env,), {"policy": policy}

        return Verifier(kind="storage", call=verify_fn, adapter=adapt)

    if kind == "vdf":
        try:
            from proofs import vdf as mod  # type: ignore

            verify_fn = getattr(mod, "verify")
        except Exception:
            from proofs import registry as reg  # type: ignore

            verify_fn = lambda envelope, **ctx: reg.verify_envelope(envelope, ctx)  # type: ignore

        def adapt(entry: dict[str, Any]) -> tuple[tuple, dict]:
            env = entry.get("envelope") or entry.get("proof") or entry
            params = entry.get("params") or {}
            return (env,), {"params": params}

        return Verifier(kind="vdf", call=verify_fn, adapter=adapt)

    raise ValueError(f"unsupported kind {kind!r}")


# ---------- Benchmark runner ----------


def run_one_kind(
    kind: str, limit: int, repeat: int, vectors_path: str | None
) -> BenchResult:
    vectors_file = Path(vectors_path) if vectors_path else None
    vectors = load_vectors(kind, override=vectors_file)
    if not vectors:
        return BenchResult(
            kind=kind,
            samples=0,
            repeats=repeat,
            successes=0,
            failures=0,
            total_calls=0,
            total_s=0.0,
            latencies_s=[],
        )

    if limit > 0:
        vectors = vectors[:limit]

    verifier = _import_verify(kind)

    latencies: list[float] = []
    successes = 0
    failures = 0

    # Warmup (JIT/caches)
    for entry in vectors[: min(3, len(vectors))]:
        args, kwargs = verifier.adapter(entry)
        try:
            _ = verifier.call(*args, **kwargs)
        except Exception:
            # swallow in warmup; real failures will be counted during timed loop
            pass

    start_total = time.perf_counter()
    for _r in range(repeat):
        for entry in vectors:
            args, kwargs = verifier.adapter(entry)
            t0 = time.perf_counter()
            try:
                _ = verifier.call(*args, **kwargs)
                successes += 1
            except Exception:
                failures += 1
            finally:
                latencies.append(time.perf_counter() - t0)
    total = time.perf_counter() - start_total

    return BenchResult(
        kind=kind,
        samples=len(vectors),
        repeats=repeat,
        successes=successes,
        failures=failures,
        total_calls=successes + failures,
        total_s=total,
        latencies_s=latencies,
    )


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description="Animica proofs throughput benchmark")
    ap.add_argument(
        "--kind",
        default="all",
        choices=["all", "hash", "ai", "quantum", "storage", "vdf"],
        help="which verifier to bench (default: all)",
    )
    ap.add_argument(
        "--limit",
        type=int,
        default=0,
        help="limit number of vector entries per kind (0 = all)",
    )
    ap.add_argument(
        "--repeat",
        type=int,
        default=200,
        help="repeat each vector entry N times (default: 200)",
    )
    ap.add_argument(
        "--vectors",
        type=str,
        default=None,
        help="override path to a vectors JSON (only when --kind != all)",
    )
    args = ap.parse_args(argv)

    kinds = (
        ["hash", "ai", "quantum", "storage", "vdf"]
        if args.kind == "all"
        else [args.kind]
    )

    print("Animica — Proof Verifiers Throughput Bench")
    print(f"vectors_dir={VECTORS_DIR}")
    print(f"kinds={kinds}, limit={args.limit or 'all'}, repeat={args.repeat}\n")

    results: list[BenchResult] = []
    for k in kinds:
        res = run_one_kind(
            k, limit=args.limit, repeat=args.repeat, vectors_path=args.vectors
        )
        results.append(res)
        if res.total_calls == 0:
            print(f"{k:8s} | (no vectors found — skipped)")
        else:
            print(res.render())
        print()

    # Summary
    total_calls = sum(r.total_calls for r in results)
    total_time = sum(r.total_s for r in results)
    if total_calls > 0 and total_time > 0:
        print(
            f"TOTAL     | calls={total_calls:,}  time={total_time:.3f}s  → {total_calls/total_time:,.0f} ops/s"
        )

    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
