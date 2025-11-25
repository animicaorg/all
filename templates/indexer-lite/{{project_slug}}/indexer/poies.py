"""
PoIES analysis helpers for the {{ project_slug }} Indexer Lite.

This module computes derived consensus health indicators from block headers and
lightweight chain context. It is intentionally defensive about missing fields:
if a header does not expose the metric you want (e.g., `gamma`, `psi`, or a
`mix` vector), the calculators degrade gracefully and annotate the result.

What is "PoIES" here?
---------------------
We treat PoIES as a *family of fairness/participation/entropy* signals used to
monitor liveness and decentralization over a sliding window of recent blocks.
Concretely, we compute:

- **Γ (gamma)**                    : difficulty/threshold-like control variable (if present)
- **ψ (psi)**                      : instantaneous acceptance pressure (if present)
- **Participation rate**           : votes / eligible (or 1.0 if unknown)
- **Producer share & fairness**    : producer block share, HHI and Gini indices
- **Mix entropy**                  : Shannon entropy of the block's randomness / mix vector (if present)
- **Lateness**                     : deviation from target block time (if inferable)

You can feed this into dashboards or Prometheus exporters (see `as_prom()`).

Typical usage
-------------
from indexer.config import from_env
from indexer.rpc import JsonRpcClient
from indexer.poies import analyze_range, RollingAnalyzer

cfg = from_env()
async with JsonRpcClient(cfg) as rpc:
    scores, summary = await analyze_range(rpc, start=0, stop=1023)
    print(summary)

CLI
---
List recent summary over a sliding window (default 256 blocks):

$ python -m indexer.poies head --window 256

Compute and print a JSON summary for an explicit range:

$ python -m indexer.poies range --from 0 --to 8191
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import math
from collections import Counter, defaultdict
from dataclasses import dataclass, asdict
from statistics import fmean
from typing import Any, Dict, Iterable, List, Mapping, MutableMapping, Optional, Sequence, Tuple, Union, cast

from .config import IndexerConfig, from_env
from .rpc import JsonRpcClient

Json = Mapping[str, Any]


# ----------------------------- utils --------------------------------------- #

def _hx(v: Union[str, int, None], default: Optional[int] = None) -> Optional[int]:
    if v is None:
        return default
    if isinstance(v, int):
        return v
    if isinstance(v, str):
        try:
            return int(v, 16) if v.startswith("0x") else int(v)
        except Exception:
            return default
    return default


def _shannon_entropy(weights: Mapping[str, Union[int, float]]) -> Optional[float]:
    total = 0.0
    for _, w in weights.items():
        try:
            total += float(w)
        except Exception:
            continue
    if total <= 0.0:
        return None
    h = 0.0
    for w in weights.values():
        p = float(w) / total if total else 0.0
        if p > 0.0:
            h -= p * math.log2(p)
    return h


def _gini_from_shares(shares: Sequence[float]) -> float:
    """
    Gini coefficient from producer shares (summing to 1).
    0 = perfect equality, 1 = extreme inequality.
    """
    n = len(shares)
    if n == 0:
        return 0.0
    sorted_s = sorted(shares)
    cum = 0.0
    for i, s in enumerate(sorted_s, start=1):
        cum += i * s
    return (2 * cum) / (n * sum(sorted_s)) - (n + 1) / n


def _hhi_from_shares(shares: Sequence[float]) -> float:
    """
    Herfindahl–Hirschman Index (sum of squared shares).
    Lower is better (more decentralized).
    """
    return sum(s * s for s in shares)


# ----------------------------- data models --------------------------------- #

@dataclass
class PoIESScore:
    number: int
    hash: Optional[str]
    producer: Optional[str]

    gamma: Optional[int]              # control variable (Γ)
    psi: Optional[int]                # instantaneous pressure (ψ)

    participation_rate: Optional[float]
    mix_entropy: Optional[float]
    lateness_s: Optional[float]

    tags: Tuple[str, ...]             # notes about fallbacks/assumptions

    def as_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        return d


@dataclass
class PoIESWindowSummary:
    window: int
    start: int
    stop: int

    avg_gamma: Optional[float]
    avg_psi: Optional[float]
    avg_participation: Optional[float]
    avg_mix_entropy: Optional[float]
    avg_lateness_s: Optional[float]

    # fairness (over producers in window)
    producers: Dict[str, int]
    shares: Dict[str, float]
    gini: float
    hhi: float

    # misc
    missing_gamma: int
    missing_psi: int
    missing_mix: int

    def as_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        return d

    # Prometheus-friendly snapshot
    def as_prom(self, namespace: str = "poies") -> str:
        lines = []
        ns = namespace

        def gauge(name: str, value: Optional[float], labels: Optional[Dict[str, str]] = None) -> None:
            if value is None:
                return
            lab = ""
            if labels:
                inner = ",".join(f'{k}="{v}"' for k, v in labels.items())
                lab = f"{{{inner}}}"
            lines.append(f"{ns}_{name}{lab} {value}")

        gauge("avg_gamma", self.avg_gamma)
        gauge("avg_psi", self.avg_psi)
        gauge("avg_participation", self.avg_participation)
        gauge("avg_mix_entropy", self.avg_mix_entropy)
        gauge("avg_lateness_seconds", self.avg_lateness_s)
        gauge("gini", self.gini)
        gauge("hhi", self.hhi)

        for addr, cnt in self.producers.items():
            gauge("producer_blocks", float(cnt), {"producer": addr})

        for addr, share in self.shares.items():
            gauge("producer_share", share, {"producer": addr})

        gauge("missing_gamma", float(self.missing_gamma))
        gauge("missing_psi", float(self.missing_psi))
        gauge("missing_mix", float(self.missing_mix))
        return "\n".join(lines) + ("\n" if lines else "")


# ----------------------------- calculators --------------------------------- #

def score_block(block: Json, *, target_block_time_s: Optional[float] = None, parent_ts: Optional[int] = None) -> PoIESScore:
    """
    Compute PoIES-style score for a single block. Best-effort extraction:

    - producer: header['miner'] | header['producer'] | header['coinbase'] | None
    - gamma   : header['consensus']['gamma'] | header['gamma'] | None (hex or int)
    - psi     : header['consensus']['psi']   | header['psi']   | None (hex or int)
    - participation: votes/eligible if present in header['consensus']
    - mix_entropy: entropy(header['consensus']['mix'] | header['mix'] | header['randomness'])
    - lateness: if timestamps for block and parent are known and target is set.

    Missing inputs produce None values; we also attach `tags` to explain fallbacks.
    """
    tags: List[str] = []

    number = _hx(block.get("number"), 0) or 0
    bhash = cast(Optional[str], block.get("hash"))
    header = block  # may be nested differently in some RPCs; we treat block as header-like

    # producer / coinbase extraction
    producer = cast(Optional[str], header.get("miner")) or cast(Optional[str], header.get("producer")) or cast(Optional[str], header.get("coinbase"))  # noqa: E501
    if producer is None:
        tags.append("missing_producer")

    # consensus envelope
    cons = cast(Optional[Mapping[str, Any]], header.get("consensus")) or {}

    gamma = _hx(cons.get("gamma") if cons else None) or _hx(header.get("gamma"))
    if gamma is None:
        tags.append("missing_gamma")

    psi = _hx(cons.get("psi") if cons else None) or _hx(header.get("psi"))
    if psi is None:
        tags.append("missing_psi")

    # participation (best effort)
    participation: Optional[float] = None
    votes = _hx(cons.get("votes") if cons else None)
    elig = _hx(cons.get("eligibleVotes") if cons else None)
    if votes is not None and elig and elig > 0:
        participation = min(1.0, max(0.0, float(votes) / float(elig)))
    elif "participation" in (cons or {}):
        try:
            participation = float(cons["participation"])  # already a float 0..1
        except Exception:
            participation = None
    else:
        tags.append("missing_participation")

    # mix entropy (prefer dict of category->weight)
    mix_entropy: Optional[float] = None
    mix = cast(Optional[Mapping[str, Any]], cons.get("mix") if cons else None) or cast(Optional[Mapping[str, Any]], header.get("mix"))  # noqa: E501
    rand = cast(Optional[Mapping[str, Any]], header.get("randomness"))  # fallback alt location
    if isinstance(mix, Mapping):
        mix_entropy = _shannon_entropy({k: float(v) for k, v in mix.items() if isinstance(v, (int, float))})
    elif isinstance(rand, Mapping):
        # treat "randomness" content (e.g., {'beacon': 128, 'quantum': 64, 'local': 16})
        mix_entropy = _shannon_entropy({k: float(v) for k, v in rand.items() if isinstance(v, (int, float))})
    if mix_entropy is None:
        tags.append("missing_mix")

    # lateness (requires timestamps and target)
    lateness_s: Optional[float] = None
    ts = _hx(header.get("timestamp"))
    if ts is not None and parent_ts is not None and target_block_time_s:
        dt = float(ts - parent_ts)
        lateness_s = dt - float(target_block_time_s)

    return PoIESScore(
        number=number,
        hash=bhash,
        producer=producer,
        gamma=gamma,
        psi=psi,
        participation_rate=participation,
        mix_entropy=mix_entropy,
        lateness_s=lateness_s,
        tags=tuple(tags),
    )


class RollingAnalyzer:
    """
    Maintains rolling statistics over recent blocks to estimate fairness (Gini/HHI),
    participation, entropy, and control variables.

    Use `push(block)` to add a block; call `summary()` to get a `PoIESWindowSummary`.
    """

    def __init__(self, *, window: int = 256, target_block_time_s: Optional[float] = None, log: Optional[logging.Logger] = None) -> None:  # noqa: E501
        self.window = max(1, int(window))
        self.target_block_time_s = target_block_time_s
        self.log = (log or logging.getLogger("indexer.poies")).getChild("rolling")

        self._scores: List[PoIESScore] = []
        self._producers: Counter[str] = Counter()

        self._last_ts_by_num: Dict[int, int] = {}

    def push(self, block: Json) -> PoIESScore:
        parent_ts = None
        parent_hex = cast(Optional[str], block.get("parentHash"))
        # Some RPCs do not carry parent timestamp; we track via cache when the parent rolled through.
        # Caller may also call `seed_parent_timestamp(number, timestamp)` to assist.
        # We look up parent's timestamp by (block.number - 1) if present.
        num = _hx(block.get("number"))
        if num is not None and (num - 1) in self._last_ts_by_num:
            parent_ts = self._last_ts_by_num[num - 1]

        sc = score_block(block, target_block_time_s=self.target_block_time_s, parent_ts=parent_ts)
        self._scores.append(sc)
        if sc.producer:
            self._producers[sc.producer] += 1

        # keep only window
        if len(self._scores) > self.window:
            dropped = self._scores.pop(0)
            if dropped.producer:
                self._producers[dropped.producer] -= 1
                if self._producers[dropped.producer] <= 0:
                    del self._producers[dropped.producer]

        # cache timestamp for lateness calc of next block
        ts = _hx(block.get("timestamp"))
        if num is not None and ts is not None:
            self._last_ts_by_num[num] = ts
            # trim older cache
            prune_before = num - 2
            for k in list(self._last_ts_by_num.keys()):
                if k < prune_before:
                    del self._last_ts_by_num[k]

        return sc

    def _avg(self, vals: Iterable[Optional[Union[int, float]]]) -> Optional[float]:
        xs = [float(v) for v in vals if v is not None]
        return fmean(xs) if xs else None

    def summary(self) -> Optional[PoIESWindowSummary]:
        if not self._scores:
            return None
        start = self._scores[0].number
        stop = self._scores[-1].number

        avg_gamma = self._avg(sc.gamma for sc in self._scores)
        avg_psi = self._avg(sc.psi for sc in self._scores)
        avg_part = self._avg(sc.participation_rate for sc in self._scores)
        avg_mix = self._avg(sc.mix_entropy for sc in self._scores)
        avg_lat = self._avg(sc.lateness_s for sc in self._scores)

        total_blocks = sum(self._producers.values())
        shares: Dict[str, float] = {}
        if total_blocks > 0:
            for p, c in self._producers.items():
                shares[p] = c / total_blocks

        gini = _gini_from_shares(list(shares.values())) if shares else 0.0
        hhi = _hhi_from_shares(list(shares.values())) if shares else 0.0

        missing_gamma = sum(1 for sc in self._scores if sc.gamma is None)
        missing_psi = sum(1 for sc in self._scores if sc.psi is None)
        missing_mix = sum(1 for sc in self._scores if sc.mix_entropy is None)

        return PoIESWindowSummary(
            window=len(self._scores),
            start=start,
            stop=stop,
            avg_gamma=avg_gamma,
            avg_psi=avg_psi,
            avg_participation=avg_part,
            avg_mix_entropy=avg_mix,
            avg_lateness_s=avg_lat,
            producers=dict(self._producers),
            shares=shares,
            gini=gini,
            hhi=hhi,
            missing_gamma=missing_gamma,
            missing_psi=missing_psi,
            missing_mix=missing_mix,
        )


# ----------------------------- range helpers -------------------------------- #

async def analyze_range(
    rpc: JsonRpcClient,
    *,
    start: int,
    stop: int,
    window: int = 256,
    full_txs: bool = False,
    target_block_time_s: Optional[float] = None,
) -> Tuple[List[PoIESScore], PoIESWindowSummary]:
    """
    Convenience: pull blocks in [start, stop], compute per-block scores and a window summary.

    Note: we pull blocks in batches using the client's range method. Some RPC servers may
    return `null` for blocks not yet present; we ignore those.
    """
    log = logging.getLogger("indexer.poies.range")
    ra = RollingAnalyzer(window=window, target_block_time_s=target_block_time_s, log=log)
    scores: List[PoIESScore] = []

    cur = start
    # prefer the client's configured batch size if available, else a safe default
    max_batch = getattr(rpc, "max_batch", None) or 25

    while cur <= stop:
        chunk_end = min(cur + max_batch - 1, stop)
        blocks = cast(List[Json], await rpc.get_block_range(cur, chunk_end, full_txs=full_txs, max_batch=max_batch))
        for b in blocks:
            if not b:
                continue
            sc = ra.push(b)
            scores.append(sc)
        cur = chunk_end + 1

    summary = ra.summary()
    if summary is None:
        # empty window; synthesize an empty one for the caller
        summary = PoIESWindowSummary(
            window=0, start=start, stop=stop,
            avg_gamma=None, avg_psi=None, avg_participation=None,
            avg_mix_entropy=None, avg_lateness_s=None,
            producers={}, shares={}, gini=0.0, hhi=0.0,
            missing_gamma=0, missing_psi=0, missing_mix=0,
        )
    return scores, summary


# ----------------------------- CLI ----------------------------------------- #

def _parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="PoIES window analysis")
    sub = p.add_subparsers(dest="cmd", required=True)

    head = sub.add_parser("head", help="summarize last N blocks (default 256)")
    head.add_argument("--window", type=int, default=256)
    head.add_argument("--target-block-time", type=float, default=None, help="seconds")

    rng = sub.add_parser("range", help="summarize explicit block range [from, to]")
    rng.add_argument("--from", dest="start", type=int, required=True)
    rng.add_argument("--to", dest="stop", type=int, required=True)
    rng.add_argument("--window", type=int, default=256)
    rng.add_argument("--target-block-time", type=float, default=None, help="seconds")

    p.add_argument("--json", action="store_true", help="emit JSON instead of pretty text")
    p.add_argument("--log-level", default="INFO")
    return p.parse_args(argv)


async def _amain(ns: argparse.Namespace) -> int:
    logging.basicConfig(
        level=getattr(logging, ns.log_level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    cfg: IndexerConfig = from_env()
    async with JsonRpcClient(cfg) as rpc:
        if ns.cmd == "head":
            head = await rpc.block_number()
            start = max(0, head - (ns.window - 1))
            _, summary = await analyze_range(
                rpc, start=start, stop=head, window=ns.window, full_txs=False,
                target_block_time_s=ns.target_block_time,
            )
            if ns.json:
                print(json.dumps(summary.as_dict(), separators=(",", ":"), sort_keys=True))
            else:
                _pretty_print_summary(summary)
        elif ns.cmd == "range":
            _, summary = await analyze_range(
                rpc, start=ns.start, stop=ns.stop, window=ns.window, full_txs=False,
                target_block_time_s=ns.target_block_time,
            )
            if ns.json:
                print(json.dumps(summary.as_dict(), separators=(",", ":"), sort_keys=True))
            else:
                _pretty_print_summary(summary)
        else:
            raise SystemExit(f"unknown command {ns.cmd}")
    return 0


def _pretty_print_summary(s: PoIESWindowSummary) -> None:
    print(f"PoIES summary for blocks [{s.start}, {s.stop}] (window={s.window})")
    print("")
    def fmt(x: Optional[float], nd=4) -> str:
        return f"{x:.{nd}f}" if x is not None else "n/a"

    print(f"  avg Γ (gamma):          {fmt(s.avg_gamma)}")
    print(f"  avg ψ (psi):            {fmt(s.avg_psi)}")
    print(f"  avg participation:      {fmt(s.avg_participation)}")
    print(f"  avg mix entropy:        {fmt(s.avg_mix_entropy)} bits")
    print(f"  avg lateness:           {fmt(s.avg_lateness_s)} s")
    print("")
    print(f"  producers in window:    {len(s.producers)}")
    if s.producers:
        top = sorted(s.producers.items(), key=lambda kv: kv[1], reverse=True)[:10]
        print("  top producers (count, share):")
        for addr, cnt in top:
            share = s.shares.get(addr, 0.0)
            print(f"    {addr}: {cnt} ({share:.2%})")
    print("")
    print(f"  fairness (lower is better):")
    print(f"    HHI:                   {s.hhi:.6f}")
    print(f"    Gini:                  {s.gini:.6f}")
    print("")
    print(f"  missing fields over window:")
    print(f"    missing Γ:             {s.missing_gamma}")
    print(f"    missing ψ:             {s.missing_psi}")
    print(f"    missing mix:           {s.missing_mix}")


def main(argv: Optional[List[str]] = None) -> int:
    ns = _parse_args(argv)
    try:
        return asyncio.run(_amain(ns))
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
