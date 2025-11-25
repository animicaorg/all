#!/usr/bin/env python3
"""
bench_poies.py
==============

Quick PoIES (Proof-of-Integrated External Services) scoring bench:

- Simulate batches (candidate blocks) of proofs and estimate acceptance rate
  using the canonical inequality:  H(u) + Σψ >= Θ
  where:
    * u ~ Uniform(0,1] (per-batch randomness draw),
    * H(u) = -ln(u)   (converted to micro-nats),
    * ψ are per-proof contributions (already "policy-mapped"),
    * Θ is the current acceptance threshold in "micro-nats".

- Or ingest a JSON file of proof ψ values and score them in batches.

This tool is intentionally *lightweight* and self-contained so it doesn’t pull
the whole node stack. It tolerates a variety of policy file shapes and will
fallback to sane defaults when fields are missing.

Examples
--------
# 1) Simulate 10k candidate blocks, each with 40 proofs, print acceptance %:
python -m consensus.cli.bench_poies simulate \
  --theta 5000000 --blocks 10000 --batch-size 40 \
  --mix "hash=0.65,ai=0.20,quantum=0.10,storage=0.04,vdf=0.01" \
  --seed 42

# 2) Same, but pull Θ and caps from spec files:
python -m consensus.cli.bench_poies simulate \
  --params ./spec/params.yaml \
  --policy ./spec/poies_policy.yaml \
  --blocks 5000 --batch-size 32 --seed 7

# 3) Score from a JSON file containing an array of proof psi entries:
#    [{"type":"hash","psi_micro":12000}, {"type":"ai","psi_micro":350000}, ...]
python -m consensus.cli.bench_poies from-json \
  --params ./spec/params.yaml \
  --file ./spec/test_vectors/proofs.json \
  --batch-size 32
"""

from __future__ import annotations

import argparse
import json
import math
import os
import random
import statistics
import sys
from dataclasses import dataclass
from typing import Dict, Iterable, List, Optional, Tuple

# Optional PyYAML; we degrade gracefully if missing.
try:
    import yaml  # type: ignore
except Exception:  # pragma: no cover
    yaml = None


MICRO = 1_000_000  # micro-nats scale for Θ and ψ


# ------------------------------ Data structures ------------------------------ #

@dataclass
class SimCaps:
    """Simple per-type cap for generating ψ values (µ-nats)."""
    cap_micro: int


DEFAULT_TYPE_CAPS: Dict[str, SimCaps] = {
    "hash":    SimCaps(cap_micro=150_000),
    "ai":      SimCaps(cap_micro=800_000),
    "quantum": SimCaps(cap_micro=1_200_000),
    "storage": SimCaps(cap_micro=120_000),
    "vdf":     SimCaps(cap_micro=90_000),
}


def load_params_theta(path: Optional[str]) -> Optional[int]:
    """
    Load Θ (micro-nats) from spec/params.yaml if available.
    Expected (tolerant) keys:
      consensus:
        theta_micro: <int>
      or
        theta_start_micro: <int>
    """
    if not path:
        return None
    if yaml is None:
        print("[warn] PyYAML not installed; --params ignored", file=sys.stderr)
        return None
    with open(path, "r", encoding="utf-8") as f:
        doc = yaml.safe_load(f)
    # tolerant lookup
    c = (doc or {}).get("consensus", {})
    theta = c.get("theta_micro") or c.get("theta_start_micro")
    if isinstance(theta, int) and theta > 0:
        return theta
    return None


def load_policy_caps(path: Optional[str]) -> Dict[str, SimCaps]:
    """
    Load per-type ψ caps (µ-nats) from spec/poies_policy.yaml if available.
    We accept several plausible shapes, for example:

    types:
      hash:
        psi_cap_micro: 150000
      ai:
        psi_cap_micro: 800000
      quantum:
        psi_cap_micro: 1200000
      storage:
        psi_cap_micro: 120000
      vdf:
        psi_cap_micro: 90000

    If missing, returns DEFAULT_TYPE_CAPS.
    """
    if not path or yaml is None:
        return DEFAULT_TYPE_CAPS
    try:
        with open(path, "r", encoding="utf-8") as f:
            doc = yaml.safe_load(f) or {}
        types = doc.get("types", {}) or doc.get("caps", {}) or {}
        caps: Dict[str, SimCaps] = {}
        for t, obj in types.items():
            if isinstance(obj, dict):
                v = obj.get("psi_cap_micro") or obj.get("cap_micro") or obj.get("psiCapMicro")
                if isinstance(v, int) and v > 0:
                    caps[str(t)] = SimCaps(cap_micro=int(v))
        # Merge defaults for any missing types
        merged = dict(DEFAULT_TYPE_CAPS)
        merged.update(caps)
        return merged
    except Exception as e:  # pragma: no cover
        print(f"[warn] failed to parse policy caps from {path}: {e}", file=sys.stderr)
        return DEFAULT_TYPE_CAPS


# ------------------------------ Scoring helpers ------------------------------ #

def H_u_micro(rng: random.Random) -> int:
    """
    Compute H(u) in micro-nats where u ~ Uniform(0,1].
    H(u) = -ln(u)
    """
    # Clip u away from 0 to avoid INF; use open interval (0,1]
    u = max(sys.float_info.min, rng.random())
    return int(round(-math.log(u) * MICRO))


def draw_psi_micro_for_type(t: str, caps: Dict[str, SimCaps], rng: random.Random) -> int:
    """
    Draw a ψ value (µ-nats) for a given proof type using a light-tailed distribution
    bounded by the cap. We bias towards smaller ψ with a square of U to emulate
    "many small, few large" contributions.
    """
    cap = caps.get(t, DEFAULT_TYPE_CAPS.get(t, SimCaps(cap_micro=100_000))).cap_micro
    # U in (0,1], bias with square (or use lognormal-lite) then scale by cap
    u = max(sys.float_info.min, rng.random())
    biased = u * u  # many small values
    val = int(biased * cap)
    if val < 1:
        val = 1
    return min(val, cap)


def parse_mix(mix: str) -> List[Tuple[str, float]]:
    """
    Parse "hash=0.6,ai=0.2,quantum=0.1,storage=0.08,vdf=0.02" into a list of (type, weight).
    Normalizes to sum 1.0 if necessary.
    """
    pairs: List[Tuple[str, float]] = []
    for part in mix.split(","):
        part = part.strip()
        if not part:
            continue
        if "=" not in part:
            raise ValueError(f"Bad mix component: {part}")
        k, v = part.split("=", 1)
        k = k.strip()
        v = float(v.strip())
        if v < 0:
            raise ValueError("weights must be >= 0")
        pairs.append((k, v))
    s = sum(w for _, w in pairs) or 1.0
    return [(k, (w / s)) for k, w in pairs]


def choose_type(weights: List[Tuple[str, float]], rng: random.Random) -> str:
    r = rng.random()
    acc = 0.0
    for t, w in weights:
        acc += w
        if r <= acc:
            return t
    return weights[-1][0]


@dataclass
class BatchResult:
    accepted: bool
    H_micro: int
    sum_psi_micro: int
    per_type_psi: Dict[str, int]


def score_batch_random(
    theta_micro: int,
    batch_size: int,
    caps: Dict[str, SimCaps],
    mix_weights: List[Tuple[str, float]],
    rng: random.Random,
) -> BatchResult:
    per_type: Dict[str, int] = {}
    sum_psi = 0
    for _ in range(batch_size):
        t = choose_type(mix_weights, rng)
        psi = draw_psi_micro_for_type(t, caps, rng)
        per_type[t] = per_type.get(t, 0) + psi
        sum_psi += psi
    Hmicro = H_u_micro(rng)
    accepted = (Hmicro + sum_psi) >= theta_micro
    return BatchResult(accepted=accepted, H_micro=Hmicro, sum_psi_micro=sum_psi, per_type_psi=per_type)


def score_batches_from_list(
    theta_micro: int, entries: List[Dict], batch_size: int, rng: random.Random, psi_field: str = "psi_micro"
) -> List[BatchResult]:
    """
    entries: list of { "type": str, "psi_micro": int } (extra fields ignored)
    Consumes entries in sequence in batches of size batch_size.
    """
    results: List[BatchResult] = []
    idx = 0
    n = len(entries)
    while idx < n:
        per_type: Dict[str, int] = {}
        sum_psi = 0
        end = min(n, idx + batch_size)
        for j in range(idx, end):
            e = entries[j]
            t = str(e.get("type", "unknown"))
            psi = int(e.get(psi_field, e.get("psi", 0)))
            if psi < 0:
                psi = 0
            per_type[t] = per_type.get(t, 0) + psi
            sum_psi += psi
        Hmicro = H_u_micro(rng)
        accepted = (Hmicro + sum_psi) >= theta_micro
        results.append(BatchResult(accepted, Hmicro, sum_psi, per_type))
        idx = end
    return results


def summarize(results: List[BatchResult]) -> Dict:
    total = len(results)
    acc = sum(1 for r in results if r.accepted)
    acc_pct = (100.0 * acc / total) if total else 0.0
    sums = [r.sum_psi_micro for r in results]
    Hs = [r.H_micro for r in results]
    # Aggregate per-type psi share
    per_type_total: Dict[str, int] = {}
    for r in results:
        for t, v in r.per_type_psi.items():
            per_type_total[t] = per_type_total.get(t, 0) + v
    total_psi = sum(per_type_total.values()) or 1
    per_type_share = {t: v / total_psi for t, v in sorted(per_type_total.items(), key=lambda kv: -kv[1])}
    return {
        "batches": total,
        "accepted": acc,
        "acceptance_pct": acc_pct,
        "sum_psi_micro": {
            "avg": statistics.fmean(sums) if sums else 0.0,
            "p50": statistics.median(sums) if sums else 0.0,
            "p95": _percentile(sums, 95) if sums else 0.0,
            "max": max(sums) if sums else 0,
        },
        "H_micro": {
            "avg": statistics.fmean(Hs) if Hs else 0.0,
            "p50": statistics.median(Hs) if Hs else 0.0,
            "p95": _percentile(Hs, 95) if Hs else 0.0,
            "max": max(Hs) if Hs else 0,
        },
        "per_type_share": per_type_share,
    }


def _percentile(xs: List[int], p: float) -> float:
    if not xs:
        return 0.0
    xs2 = sorted(xs)
    k = (len(xs2) - 1) * (p / 100.0)
    f = math.floor(k)
    c = math.ceil(k)
    if f == c:
        return float(xs2[int(k)])
    d0 = xs2[f] * (c - k)
    d1 = xs2[c] * (k - f)
    return float(d0 + d1)


# ---------------------------------- CLI ------------------------------------- #

def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(prog="bench_poies", description="PoIES scoring bench")
    sub = ap.add_subparsers(dest="cmd", required=True)

    sim = sub.add_parser("simulate", help="simulate random batches and print acceptance %")
    sim.add_argument("--theta", type=int, help="Θ (micro-nats). If omitted, try --params")
    sim.add_argument("--params", type=str, help="Path to spec/params.yaml (to read Θ)")
    sim.add_argument("--policy", type=str, help="Path to spec/poies_policy.yaml (for caps)")
    sim.add_argument("--blocks", type=int, default=5000, help="number of simulated batches (default: 5000)")
    sim.add_argument("--batch-size", type=int, default=32, help="proofs per batch (default: 32)")
    sim.add_argument("--mix", type=str,
                     default="hash=0.65,ai=0.20,quantum=0.10,storage=0.04,vdf=0.01",
                     help="type weights (comma sep), normalized if needed")
    sim.add_argument("--seed", type=int, default=1, help="PRNG seed (default: 1)")
    sim.add_argument("--json-out", type=str, help="write summary JSON to this path")

    fj = sub.add_parser("from-json", help="score batches from a JSON array of proof entries")
    fj.add_argument("--file", type=str, required=True, help="JSON file with array of {type, psi_micro}")
    fj.add_argument("--psi-field", type=str, default="psi_micro", help="field name for ψ values (default: psi_micro)")
    fj.add_argument("--theta", type=int, help="Θ (micro-nats). If omitted, try --params")
    fj.add_argument("--params", type=str, help="Path to spec/params.yaml (to read Θ)")
    fj.add_argument("--batch-size", type=int, default=32, help="proofs per batch (default: 32)")
    fj.add_argument("--seed", type=int, default=2, help="PRNG seed (default: 2)")
    fj.add_argument("--json-out", type=str, help="write summary JSON to this path")

    args = ap.parse_args(argv)

    if args.cmd == "simulate":
        theta = args.theta or load_params_theta(args.params)
        if not theta:
            print("[error] Θ is required (use --theta or --params pointing to spec/params.yaml)", file=sys.stderr)
            return 2

        caps = load_policy_caps(args.policy)
        weights = parse_mix(args.mix)
        rng = random.Random(args.seed)

        results: List[BatchResult] = []
        for _ in range(int(args.blocks)):
            results.append(
                score_batch_random(
                    theta_micro=int(theta),
                    batch_size=int(args.batch_size),
                    caps=caps,
                    mix_weights=weights,
                    rng=rng,
                )
            )
        summary = summarize(results)
        _print_summary(theta, args.batch_size, summary)
        if args.json_out:
            with open(args.json_out, "w", encoding="utf-8") as f:
                json.dump({"theta_micro": theta, "batch_size": args.batch_size, "summary": summary}, f, indent=2)
        return 0

    if args.cmd == "from-json":
        theta = args.theta or load_params_theta(args.params)
        if not theta:
            print("[error] Θ is required (use --theta or --params pointing to spec/params.yaml)", file=sys.stderr)
            return 2
        with open(args.file, "r", encoding="utf-8") as f:
            entries = json.load(f)
            if not isinstance(entries, list):
                raise SystemExit("[error] JSON must be an array of entries")

        rng = random.Random(args.seed)
        results = score_batches_from_list(theta, entries, args.batch_size, rng, psi_field=args.psi_field)
        summary = summarize(results)
        _print_summary(theta, args.batch_size, summary, label=os.path.basename(args.file))
        if args.json_out:
            with open(args.json_out, "w", encoding="utf-8") as f:
                json.dump({"theta_micro": theta, "batch_size": args.batch_size, "summary": summary}, f, indent=2)
        return 0

    return 0


def _print_summary(theta_micro: int, batch_size: int, s: Dict, label: Optional[str] = None) -> None:
    hdr = f"PoIES Bench — Θ={theta_micro:,} µnats, batch={batch_size}"
    if label:
        hdr += f", source={label}"
    print(hdr)
    print("-" * len(hdr))
    print(f"Acceptance: {s['accepted']}/{s['batches']}  ({s['acceptance_pct']:.2f}%)")
    sp = s["sum_psi_micro"]
    hp = s["H_micro"]
    print(f"Σψ (µnats):  avg={sp['avg']:.1f}  p50={sp['p50']:.1f}  p95={sp['p95']:.1f}  max={sp['max']:,}")
    print(f"H(u) (µnats): avg={hp['avg']:.1f}  p50={hp['p50']:.1f}  p95={hp['p95']:.1f}  max={hp['max']:,}")
    if s["per_type_share"]:
        print("Per-type ψ share:")
        for t, share in s["per_type_share"].items():
            print(f"  - {t:<8} {share*100:6.2f}%")
    print()


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
