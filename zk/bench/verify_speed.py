#!/usr/bin/env python3
"""
zk/bench/verify_speed.py — per-scheme verifier micro-bench with JSON output

Runs small, reproducible verification benches for:
  - Groth16 (BN254, SnarkJS JSON fixtures)
  - PLONK+KZG (BN254, PlonkJS JSON fixtures)
  - STARK (toy FRI Merkle demo)

Outputs a single JSON object to stdout (or --outfile) with timing stats and
deterministic metering "units". See zk/bench/README.md for details.

Usage examples:
  python zk/bench/verify_speed.py --scheme all --iters 50 --warmup 5 --pretty
  python zk/bench/verify_speed.py --scheme groth16 --iters 25 --meter-only

Environment overrides for fixture locations:
  - ZK_GROTH16_EMBED_DIR
  - ZK_PLONK_POSEIDON_DIR
  - ZK_STARK_MERKLE_DIR
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import statistics
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# Optional: light logging when ZK_TEST_LOG=1
try:
    from zk.tests import configure_test_logging, fixture_path

    configure_test_logging()
except Exception:  # pragma: no cover

    def fixture_path(*parts: str) -> Path:
        base = Path(__file__).resolve().parent.parent / "tests" / "fixtures"
        return base.joinpath(*parts)

    def configure_test_logging() -> None:
        pass


from zk.integration.omni_hooks import zk_verify
from zk.integration.types import canonical_json_bytes

# --------- Helpers: statistics & output ---------------------------------------


def _quantile(sorted_vals: List[float], q: float) -> float:
    """
    Simple inclusive quantile (q in 0..1) using nearest-rank approach.
    """
    if not sorted_vals:
        return 0.0
    n = len(sorted_vals)
    idx = max(0, min(n - 1, int(q * (n - 1) + 0.5)))
    return sorted_vals[idx]


@dataclass
class BenchStats:
    iters: int
    ok_count: int
    mean_s: float
    median_s: float
    p90_s: float
    p95_s: float
    p99_s: float
    min_s: float
    max_s: float
    stdev_s: float

    @classmethod
    def from_samples(cls, samples: List[float], oks: List[bool]) -> "BenchStats":
        iters = len(samples)
        ok_count = sum(1 for x in oks if x)
        if iters == 0:
            return cls(0, 0, 0, 0, 0, 0, 0, 0, 0, 0)
        s = sorted(samples)
        return cls(
            iters=iters,
            ok_count=ok_count,
            mean_s=sum(samples) / iters,
            median_s=statistics.median(s),
            p90_s=_quantile(s, 0.90),
            p95_s=_quantile(s, 0.95),
            p99_s=_quantile(s, 0.99),
            min_s=s[0],
            max_s=s[-1],
            stdev_s=statistics.pstdev(samples) if iters > 1 else 0.0,
        )


def _size_bytes(obj: Any) -> int:
    return len(canonical_json_bytes(obj))


def _env_meta() -> Dict[str, Any]:
    import platform

    return {
        "python": sys.version.split()[0],
        "python_impl": platform.python_implementation(),
        "platform": platform.platform(),
        "machine": platform.machine(),
        "processor": platform.processor(),
        "env": {
            "ZK_DISABLE_NATIVE": os.getenv("ZK_DISABLE_NATIVE", "0"),
            "ZK_FORCE_PYECC": os.getenv("ZK_FORCE_PYECC", "0"),
            "PYTHONHASHSEED": os.getenv("PYTHONHASHSEED", ""),
        },
        "time": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }


# --------- Fixture loaders: build envelopes -----------------------------------


def _load_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as fh:
        return json.load(fh)


def _groth16_envelope() -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    base = Path(os.getenv("ZK_GROTH16_EMBED_DIR") or fixture_path("groth16_embedding"))
    proof_p = base / "proof.json"
    vk_p = base / "vk.json"
    if not proof_p.exists() or not vk_p.exists():
        return None, f"missing fixtures at {base} (need proof.json & vk.json)"
    proof = _load_json(proof_p)
    vk = _load_json(vk_p)
    public = proof.get("publicSignals")
    if public is None:
        pub_p = base / "public.json"
        if not pub_p.exists():
            return (
                None,
                "no public inputs found (neither proof.publicSignals nor public.json)",
            )
        public = _load_json(pub_p)
    env = {
        "kind": "groth16_bn254",
        "vk_format": "snarkjs",
        "vk": vk,
        "proof": proof,
        "public_inputs": public,
        "meta": {"circuit_id": "embedding_threshold_groth16_bn254@test"},
    }
    return env, None


def _plonk_envelope() -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    base = Path(os.getenv("ZK_PLONK_POSEIDON_DIR") or fixture_path("plonk_poseidon"))
    proof_p = base / "proof.json"
    vk_p = base / "vk.json"
    if not proof_p.exists() or not vk_p.exists():
        return None, f"missing fixtures at {base} (need proof.json & vk.json)"
    proof = _load_json(proof_p)
    vk = _load_json(vk_p)
    public = proof.get("publicSignals")
    if public is None:
        pub_p = base / "public.json"
        if not pub_p.exists():
            return (
                None,
                "no public inputs found (neither proof.publicSignals nor public.json)",
            )
        public = _load_json(pub_p)
    env = {
        "kind": "plonk_kzg_bn254",
        "vk_format": "plonkjs",
        "vk": vk,
        "proof": proof,
        "public_inputs": public,
        "meta": {"circuit_id": "poseidon_demo_plonk_kzg_bn254@test"},
    }
    return env, None


def _stark_envelope() -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    base = Path(os.getenv("ZK_STARK_MERKLE_DIR") or fixture_path("stark_merkle"))
    proof_p = base / "proof.json"
    if not proof_p.exists():
        return None, f"missing fixtures at {base} (need proof.json)"
    proof = _load_json(proof_p)

    # VK optional; synthesize minimal if absent.
    vk_p = base / "vk.json"
    if vk_p.exists():
        vk = _load_json(vk_p)
    else:
        fri = proof.get("fri_params", {}) if isinstance(proof, dict) else {}
        log_n = fri.get("log_n") or 16
        num_q = fri.get("num_rounds") or len(proof.get("queries", [])) or 1
        vk = {
            "air": "merkle_membership_v1",
            "field": fri.get("field", "bn254_fr"),
            "hash": fri.get("hash", "keccak"),
            "domain_log2": int(log_n),
            "num_queries": int(num_q),
        }

    public = proof.get("public_inputs")
    if public is None:
        pub_p = base / "public.json"
        if not pub_p.exists():
            return (
                None,
                "no public inputs found (neither proof.public_inputs nor public.json)",
            )
        public = _load_json(pub_p)

    env = {
        "kind": "stark_fri_merkle",
        "vk_format": "fri",
        "vk": vk,
        "proof": proof,
        "public_inputs": public,
        "meta": {"circuit_id": "merkle_membership_stark_demo@test"},
    }
    return env, None


# --------- Bench runner --------------------------------------------------------


def _bench_env(
    envelope: Dict[str, Any], iters: int, warmup: int, meter_only: bool
) -> Tuple[BenchStats, int]:
    # Warm-up (unmeasured)
    for _ in range(max(0, warmup)):
        zk_verify(envelope, meter_only=meter_only)

    times: List[float] = []
    oks: List[bool] = []
    units_seen: Optional[int] = None

    for _ in range(max(0, iters)):
        t0 = time.perf_counter()
        res = zk_verify(envelope, meter_only=meter_only)
        dt = time.perf_counter() - t0

        ok = bool(res.get("ok", False))
        oks.append(ok)
        times.append(dt)

        units = res.get("units")
        if isinstance(units, int):
            units_seen = units if units_seen is None else units_seen

    stats = BenchStats.from_samples(times, oks)
    return stats, (units_seen or 0)


def _report_for(
    env: Dict[str, Any], stats: BenchStats, units: int, meter_only: bool
) -> Dict[str, Any]:
    proof_b = _size_bytes(env["proof"])
    vk_b = _size_bytes(env["vk"]) if env.get("vk") is not None else 0
    env_b = _size_bytes(env)
    proof_hash = hashlib.sha3_256(canonical_json_bytes(env["proof"])).hexdigest()
    vk_hash = (
        hashlib.sha3_256(canonical_json_bytes(env["vk"])).hexdigest()
        if env.get("vk") is not None
        else None
    )

    return {
        "scheme": env["kind"],
        "vk_format": env.get("vk_format"),
        "circuit_id": (env.get("meta") or {}).get("circuit_id"),
        "meter_only": bool(meter_only),
        "iters": stats.iters,
        "ok_rate": stats.ok_count / stats.iters if stats.iters else 0.0,
        "units": units,
        "input_bytes": {"proof": proof_b, "vk": vk_b, "envelope": env_b},
        "hashes": {"proof_sha3_256": proof_hash, "vk_sha3_256": vk_hash},
        "time_s": {
            "mean": stats.mean_s,
            "median": stats.median_s,
            "p90": stats.p90_s,
            "p95": stats.p95_s,
            "p99": stats.p99_s,
            "min": stats.min_s,
            "max": stats.max_s,
            "stdev": stats.stdev_s,
        },
    }


def run_benches(
    schemes: List[str], iters: int, warmup: int, meter_only: bool
) -> Dict[str, Any]:
    loaders = {
        "groth16": _groth16_envelope,
        "plonk": _plonk_envelope,
        "stark": _stark_envelope,
    }

    if "all" in schemes:
        schemes = ["groth16", "plonk", "stark"]

    results: List[Dict[str, Any]] = []

    for name in schemes:
        loader = loaders.get(name)
        if loader is None:
            results.append(
                {"scheme": name, "skipped": True, "reason": "unknown scheme"}
            )
            continue
        env, err = loader()
        if env is None:
            results.append({"scheme": name, "skipped": True, "reason": err})
            continue

        stats, units = _bench_env(
            env, iters=iters, warmup=warmup, meter_only=meter_only
        )
        results.append(
            {
                **_report_for(env, stats, units, meter_only),
                "skipped": False,
            }
        )

    return {
        "meta": {
            **_env_meta(),
            "iters": iters,
            "warmup": warmup,
            "meter_only": bool(meter_only),
            "schemes": schemes,
        },
        "results": results,
    }


# --------- CLI ----------------------------------------------------------------


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(
        description="Per-scheme verifier micro-bench; JSON output"
    )
    ap.add_argument(
        "--scheme",
        choices=["all", "groth16", "plonk", "stark"],
        default="all",
        help="which scheme(s) to run (default: all)",
    )
    ap.add_argument(
        "--iters", type=int, default=30, help="measured iterations (default: 30)"
    )
    ap.add_argument(
        "--warmup",
        type=int,
        default=5,
        help="unmeasured warmup iterations (default: 5)",
    )
    ap.add_argument(
        "--meter-only",
        action="store_true",
        help="skip crypto; compute metering units only",
    )
    ap.add_argument("--pretty", action="store_true", help="pretty-print JSON")
    ap.add_argument("--outfile", type=Path, help="write JSON to file instead of stdout")
    args = ap.parse_args(argv)

    report = run_benches(
        schemes=[args.scheme],
        iters=max(0, args.iters),
        warmup=max(0, args.warmup),
        meter_only=bool(args.meter_only),
    )

    data = json.dumps(report, indent=2 if args.pretty else None, sort_keys=args.pretty)
    if args.outfile:
        args.outfile.parent.mkdir(parents=True, exist_ok=True)
        args.outfile.write_text(data + "\n", encoding="utf-8")
    else:
        print(data)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
