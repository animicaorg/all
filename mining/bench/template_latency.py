"""
template_latency.py
===================

Benchmarks the "block template build" + "candidate packing" path.

- If the real modules are available:
    - uses mining.templates.build_header_template(...)
    - and mining.header_packer.pack_candidate_block(...)

- Otherwise, falls back to a faithful local simulation that:
    - builds a synthetic header template (parent, height, mixSeed, Θ snapshot)
    - merklizes transactions and proof receipts
    - assembles a candidate header/body and computes summary hashes

This makes the bench runnable even before the full node is wired.

Usage
-----
    python -m mining.bench.template_latency
    # or environment overrides:
    ITER=50 TXS=300 PROOF_HASH=64 PROOF_AI=2 PROOF_Q=1 python -m mining.bench.template_latency

Reported metrics
----------------
- Per-iteration timings for:
    * build_template_ms
    * pack_candidate_ms
- Summary: min / median / p95 / max
- Derived throughput: candidates/sec

"""

from __future__ import annotations

import os
import time
import math
import random
import statistics
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

# Prefer core/multi-utils where available, otherwise use hashlib.
try:
    from core.utils.hash import sha3_256 as _sha3_256
except Exception:
    import hashlib

    def _sha3_256(b: bytes) -> bytes:
        return hashlib.sha3_256(b).digest()

# Optional real integrations (best effort)
_build_template = None
_pack_candidate = None
try:
    from mining.templates import build_header_template as _build_template  # type: ignore
except Exception:
    pass

try:
    from mining.header_packer import pack_candidate_block as _pack_candidate  # type: ignore
except Exception:
    try:
        # Some versions expose a class with method pack(...)
        from mining.header_packer import HeaderPacker  # type: ignore

        def _pack_candidate(template, txs: List[bytes], proofs: Dict[str, List[bytes]]):
            packer = HeaderPacker()
            return packer.pack(template, txs, proofs)
    except Exception:
        pass


# -----------------------------
# Fallback implementations
# -----------------------------
def _merkle_root(leaves: List[bytes]) -> bytes:
    """Canonical, SHA3-256 Merkle with lexicographically ordered pair-hash."""
    if not leaves:
        return b"\x00" * 32
    level = [ _sha3_256(x) for x in leaves ]
    while len(level) > 1:
        nxt = []
        it = iter(level)
        for a in it:
            b = next(it, a)  # duplicate last if odd
            pair = a + b if a <= b else b + a
            nxt.append(_sha3_256(pair))
        level = nxt
    return level[0]


def _synthetic_build_template(parent_hash: bytes, height: int, theta_micro: int, mix_seed: bytes) -> Dict[str, Any]:
    """Very small, deterministic header template for the bench fallback."""
    return {
        "version": 1,
        "parent_hash": parent_hash,
        "height": height,
        "timestamp": int(time.time()),
        "theta_micro": int(theta_micro),
        "mix_seed": mix_seed,
        "algo_policy_root": b"\xAB" * 32,
        "poies_policy_root": b"\xCD" * 32,
    }


def _synthetic_pack_candidate(template: Dict[str, Any],
                              txs: List[bytes],
                              proofs: Dict[str, List[bytes]]) -> Dict[str, Any]:
    """Pack a block candidate (fallback) by computing canonical roots."""
    # Derive receipts root from proof receipts (fake here: hash of body bytes)
    proof_receipts = []
    for kind in ("hash", "ai", "quantum", "storage", "vdf"):
        for p in proofs.get(kind, []):
            proof_receipts.append(_sha3_256(p))
    tx_root = _merkle_root(txs)
    proofs_root = _merkle_root(proof_receipts)
    # Fake DA root (no blobs in this bench)
    da_root = b"\x11" * 32

    header_body = (
        template["parent_hash"] +
        template["height"].to_bytes(8, "big") +
        template["timestamp"].to_bytes(8, "big") +
        template["theta_micro"].to_bytes(8, "big") +
        template["mix_seed"] +
        tx_root + proofs_root + da_root
    )
    header_hash = _sha3_256(header_body)
    header = {
        "hash": header_hash,
        "tx_root": tx_root,
        "proofs_root": proofs_root,
        "da_root": da_root,
        **template,
    }
    return {
        "header": header,
        "txs": txs,
        "proof_receipts": proof_receipts,
    }


# -----------------------------
# Data generation
# -----------------------------
def _rand_bytes(n: int, rng: random.Random) -> bytes:
    return bytes(rng.getrandbits(8) for _ in range(n))


def _make_fake_tx(rng: random.Random, avg_size: int = 250) -> bytes:
    # Mimic a compact CBOR-encoded transfer/call payload
    size = max(80, int(rng.lognormvariate(mu=math.log(avg_size), sigma=0.35)))
    return b"TX" + _rand_bytes(size - 2, rng)


def _make_fake_proof(kind: str, rng: random.Random, size_hint: int) -> bytes:
    tag = {
        "hash": b"HS",
        "ai": b"AI",
        "quantum": b"Q1",
        "storage": b"ST",
        "vdf": b"VF",
    }[kind]
    return tag + _rand_bytes(size_hint - 2, rng)


@dataclass
class BenchCfg:
    iterations: int = 50
    txs: int = 300
    avg_tx_size: int = 300
    proof_hash: int = 64
    proof_ai: int = 2
    proof_quantum: int = 1
    proof_storage: int = 4
    proof_vdf: int = 1
    seed: int = 0xBEEF_2025
    theta_micro: int = 3_000_000  # Θ ≈ 3.0 nats
    height: int = 1000


def _percentile(xs: List[float], p: float) -> float:
    if not xs:
        return 0.0
    k = (len(xs) - 1) * (p / 100.0)
    f, c = math.floor(k), math.ceil(k)
    if f == c:
        return xs[int(k)]
    return xs[f] + (xs[c] - xs[f]) * (k - f)


# -----------------------------
# Benchmark runner
# -----------------------------
def run(cfg: Optional[BenchCfg] = None) -> Dict[str, Any]:
    cfg = cfg or BenchCfg()
    rng = random.Random(cfg.seed)

    parent_hash = _sha3_256(b"GENESIS" + cfg.height.to_bytes(8, "big"))
    mix_seed = _sha3_256(b"MIX" + cfg.height.to_bytes(8, "big"))

    build_times: List[float] = []
    pack_times: List[float] = []

    use_real = (_build_template is not None) and (_pack_candidate is not None)

    for i in range(cfg.iterations):
        # fresh synthetic inputs each iteration to defeat warm caches a bit
        txs = [_make_fake_tx(rng, cfg.avg_tx_size) for _ in range(cfg.txs)]
        proofs = {
            "hash":    [_make_fake_proof("hash", rng, 64) for _ in range(cfg.proof_hash)],
            "ai":      [_make_fake_proof("ai", rng, 256) for _ in range(cfg.proof_ai)],
            "quantum": [_make_fake_proof("quantum", rng, 192) for _ in range(cfg.proof_quantum)],
            "storage": [_make_fake_proof("storage", rng, 96) for _ in range(cfg.proof_storage)],
            "vdf":     [_make_fake_proof("vdf", rng, 96) for _ in range(cfg.proof_vdf)],
        }

        t0 = time.perf_counter()
        if use_real:
            # Real template builder (signature: build_header_template(core, consensus, proofs, mempool))
            # Provide minimal shim adapters expected by the function:
            class _CoreShim:
                def head(self):
                    return {"hash": parent_hash, "height": cfg.height - 1, "mixSeed": mix_seed}

            class _ConsensusShim:
                def theta_micro(self):
                    return cfg.theta_micro

            class _ProofsShim:
                def preview_metrics(self, proofs_dict):
                    return {"hash": len(proofs_dict.get("hash", []))}

            class _MempoolShim:
                def snapshot(self, limit=None):
                    return txs

            template = _build_template(_CoreShim(), _ConsensusShim(), _ProofsShim(), _MempoolShim())
        else:
            template = _synthetic_build_template(parent_hash, cfg.height, cfg.theta_micro, mix_seed)
        t1 = time.perf_counter()

        if use_real:
            candidate = _pack_candidate(template, txs, proofs)
        else:
            candidate = _synthetic_pack_candidate(template, txs, proofs)
        t2 = time.perf_counter()

        build_times.append((t1 - t0) * 1000.0)
        pack_times.append((t2 - t1) * 1000.0)

        # Mildly perturb height & seeds between iterations
        cfg.height += 1
        parent_hash = candidate["header"]["hash"] if isinstance(candidate, dict) else _sha3_256(parent_hash)
        mix_seed = _sha3_256(mix_seed)

    def summary(xs: List[float]) -> Dict[str, float]:
        xs_sorted = sorted(xs)
        return {
            "min": min(xs_sorted),
            "median": statistics.median(xs_sorted),
            "p95": _percentile(xs_sorted, 95),
            "max": max(xs_sorted),
            "avg": statistics.fmean(xs_sorted),
        }

    total_ms = sum(build_times) + sum(pack_times)
    total_s = total_ms / 1000.0
    cps = cfg.iterations / total_s if total_s > 0 else 0.0

    return {
        "iterations": cfg.iterations,
        "use_real_path": bool(use_real),
        "build_ms": build_times,
        "pack_ms": pack_times,
        "build_summary": summary(build_times),
        "pack_summary": summary(pack_times),
        "candidates_per_sec": cps,
    }


def _fmt_summ(s: Dict[str, float]) -> str:
    return (
        f"min {s['min']:.2f} ms | med {s['median']:.2f} ms | p95 {s['p95']:.2f} ms | "
        f"avg {s['avg']:.2f} ms | max {s['max']:.2f} ms"
    )


def _env_int(name: str, default: int) -> int:
    v = os.getenv(name)
    return int(v) if v is not None and v.strip() != "" else default


def main() -> int:
    cfg = BenchCfg(
        iterations=_env_int("ITER", 50),
        txs=_env_int("TXS", 300),
        avg_tx_size=_env_int("TX_SIZE", 300),
        proof_hash=_env_int("PROOF_HASH", 64),
        proof_ai=_env_int("PROOF_AI", 2),
        proof_quantum=_env_int("PROOF_Q", 1),
        proof_storage=_env_int("PROOF_ST", 4),
        proof_vdf=_env_int("PROOF_VDF", 1),
        seed=_env_int("SEED", 0xBEEF_2025),
        theta_micro=_env_int("THETA_U", 3_000_000),
        height=_env_int("HEIGHT", 1000),
    )
    out = run(cfg)
    print("Animica template/pack latency bench")
    print(f"  iterations: {out['iterations']} | real-path: {out['use_real_path']}")
    print(f"  build: {_fmt_summ(out['build_summary'])}")
    print(f"  pack : {_fmt_summ(out['pack_summary'])}")
    print(f"  candidates/sec (overall): {out['candidates_per_sec']:.2f}")
    print()
    print("Hint: tweak workload via env, e.g.: ITER=100 TXS=500 PROOF_HASH=128 python -m mining.bench.template_latency")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
