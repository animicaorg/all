from __future__ import annotations

"""
Batch size vs runtime/memory for settlement aggregation.

This micro-benchmark simulates the settlement engine's hot path where a large
batch of payout items (provider_id, amount) is aggregated into per-provider
totals (and a treasury slice), then optionally "written" to a ledger.

Two aggregation modes are provided:
- dict  : hash-map accumulation per provider (O(N) expected, higher peak memory)
- sort  : sort by provider then linear sweep reduce (O(N log N), lower map pressure)

Skew knobs let you simulate realistic distributions (hotset gets the majority
of payouts), which can influence cache/memory behavior and grouping cardinality.

Run
----
  python -m aicf.bench.settlement_batch
  python -m aicf.bench.settlement_batch --sizes 10k,50k,200k,1m
  python -m aicf.bench.settlement_batch --providers 4096 --sizes 250k --mode dict --hotset 0.05 --hotset-share 0.8
  python -m aicf.bench.settlement_batch --sizes 100k,500k --mode sort --simulate-write --seed 1337

Environment (optional)
----------------------
AICF_BENCH_WARMUP     : Warmup items per a median run (default: 0)
AICF_BENCH_ITERATIONS : Multiplier on --sizes items (int, default: 1)
AICF_BENCH_SEED       : PRNG seed (int)

Output
------
A table (or JSON lines with --json):
  batch     providers  mode   hotset%  share%  groups  elapsed_s  items/s     ns/item  peak_mem_MB  B/item
"""

import argparse
import os
import random
import time
import tracemalloc
from dataclasses import dataclass
from typing import Dict, Iterable, List, Sequence, Tuple

# -------------------------- Utilities --------------------------


def parse_qty(s: str) -> int:
    """Parse integers with k/m/g suffixes (1k=1_000, 1m=1_000_000, 1g=1_000_000_000)."""
    s = s.replace("_", "").strip().lower()
    if not s:
        raise ValueError("empty quantity")
    mult = 1
    if s.endswith("k"):
        mult, s = 1_000, s[:-1]
    elif s.endswith("m"):
        mult, s = 1_000_000, s[:-1]
    elif s.endswith("g"):
        mult, s = 1_000_000_000, s[:-1]
    return int(s) * mult


def parse_sizes(arg: str | None) -> List[int]:
    if not arg:
        return [10_000, 50_000, 200_000, 1_000_000]
    out: List[int] = []
    for part in arg.split(","):
        part = part.strip()
        if not part:
            continue
        out.append(parse_qty(part))
    return out


def human_mb(nbytes: int) -> float:
    return nbytes / (1024.0 * 1024.0)


# -------------------------- Data model --------------------------


@dataclass(slots=True, frozen=True)
class PayoutItem:
    provider_id: int
    amount: int  # integer "units"; think of as base tokens or minimal subunits
    treasury_share: int  # share routed to treasury (already split out)


# -------------------------- Generation --------------------------


def generate_payouts(
    n: int,
    providers: int,
    rng: random.Random,
    hotset_frac: float,
    hotset_share: float,
    min_amount: int = 1,
    max_amount: int = 1000,
) -> List[PayoutItem]:
    """
    Generate `n` payout items across `providers` with optional skew:
      - `hotset_frac` of providers receive ~`hotset_share` of total items.
    Amounts are uniform ints in [min_amount, max_amount]; treasury share is 10%.
    """
    assert 0 < providers
    assert 0.0 <= hotset_frac <= 1.0
    assert 0.0 <= hotset_share <= 1.0

    hot_count = max(1, int(providers * hotset_frac)) if hotset_frac > 0 else 0
    hot_ids = list(range(hot_count))
    cold_ids = list(range(hot_count, providers))

    items: List[PayoutItem] = []
    items_append = items.append

    for _ in range(n):
        if hot_count > 0 and rng.random() < hotset_share:
            pid = rng.choice(hot_ids)
        else:
            pid = rng.randrange(providers) if not cold_ids else rng.choice(cold_ids)
        amt = rng.randint(min_amount, max_amount)
        t_share = amt // 10  # 10% to treasury for the bench
        items_append(
            PayoutItem(provider_id=pid, amount=amt - t_share, treasury_share=t_share)
        )

    return items


# -------------------------- Aggregators --------------------------


def aggregate_dict(items: Sequence[PayoutItem]) -> Tuple[Dict[int, int], int]:
    """Hash-map accumulation per provider and sum treasury."""
    per_provider: Dict[int, int] = {}
    treasury_total = 0
    pp = per_provider
    t = 0
    for it in items:
        t += it.treasury_share
        pp[it.provider_id] = pp.get(it.provider_id, 0) + it.amount
    return per_provider, t


def aggregate_sort(items: Sequence[PayoutItem]) -> Tuple[Dict[int, int], int]:
    """Sort by provider then sweep to reduce; returns dict for parity with dict mode."""
    # Build a lightweight tuple list to sort (provider_id, amount)
    pairs = [(it.provider_id, it.amount) for it in items]
    pairs.sort(key=lambda x: x[0])

    per_provider: Dict[int, int] = {}
    cur_pid = None
    cur_sum = 0
    for pid, amt in pairs:
        if cur_pid is None:
            cur_pid, cur_sum = pid, amt
        elif pid == cur_pid:
            cur_sum += amt
        else:
            per_provider[cur_pid] = cur_sum
            cur_pid, cur_sum = pid, amt
    if cur_pid is not None:
        per_provider[cur_pid] = cur_sum

    # Treasury total can be computed in a separate pass (cheap)
    treasury_total = sum(it.treasury_share for it in items)
    return per_provider, treasury_total


# -------------------------- Benchmark runner --------------------------


def run_once(
    n_items: int,
    providers: int,
    mode: str,
    seed: int,
    hotset_frac: float,
    hotset_share: float,
    simulate_write: bool,
) -> Tuple[float, int, int]:
    """
    Run a single aggregation with tracemalloc; returns (elapsed_s, peak_bytes, groups).
    """
    rng = random.Random(seed)
    items = generate_payouts(n_items, providers, rng, hotset_frac, hotset_share)

    # Start memory tracing just for the hot section
    tracemalloc.start()
    t0 = time.perf_counter()

    if mode == "dict":
        per_provider, treasury_total = aggregate_dict(items)
    elif mode == "sort":
        per_provider, treasury_total = aggregate_sort(items)
    else:
        raise ValueError(f"unknown mode: {mode}")

    groups = len(per_provider)

    if simulate_write:
        # Simulate IO/commit overhead; keep it deterministic and cheap
        # (e.g., compute a rolling checksum to avoid dead-code elimination)
        checksum = 1469598103934665603  # FNV offset
        for pid, total in per_provider.items():
            checksum ^= (pid * 1099511628211) ^ total
            checksum &= (1 << 64) - 1
        # incorporate treasury_total too
        checksum ^= treasury_total
        # Use checksum so the optimizer can't drop it
        if checksum == 0xFFFFFFFFFFFFFFFF:
            print("impossible", checksum)  # never

    elapsed = time.perf_counter() - t0
    current, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    # Free references (best-effort) before returning
    del items, per_provider
    return elapsed, peak, groups


def main(argv: List[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Benchmark settlement aggregation: batch size vs runtime/memory."
    )
    parser.add_argument(
        "--sizes",
        type=str,
        default=None,
        help="Comma list of batch sizes (supports k/m/g suffix). Default: 10k,50k,200k,1m",
    )
    parser.add_argument(
        "--providers",
        type=int,
        default=4096,
        help="Total distinct providers in the registry.",
    )
    parser.add_argument(
        "--mode", choices=["dict", "sort"], default="dict", help="Aggregation strategy."
    )
    parser.add_argument(
        "--hotset",
        type=float,
        default=0.05,
        help="Fraction of providers constituting the hot set (0..1).",
    )
    parser.add_argument(
        "--hotset-share",
        type=float,
        default=0.80,
        help="Fraction of items routed to the hot set (0..1).",
    )
    parser.add_argument(
        "--seed",
        type=int,
        default=None,
        help="PRNG seed (default env AICF_BENCH_SEED or 42).",
    )
    parser.add_argument(
        "--simulate-write",
        action="store_true",
        help="Simulate committing per-provider totals to a ledger.",
    )
    parser.add_argument(
        "--no-header", action="store_true", help="Do not print the header row."
    )
    parser.add_argument(
        "--json", action="store_true", help="Emit JSON lines for each run."
    )
    args = parser.parse_args(argv)

    warmup_items = int(os.getenv("AICF_BENCH_WARMUP", "0") or "0")
    iter_mult = int(os.getenv("AICF_BENCH_ITERATIONS", "1") or "1")
    env_seed = os.getenv("AICF_BENCH_SEED")
    seed = args.seed if args.seed is not None else (int(env_seed) if env_seed else 42)

    sizes = parse_sizes(args.sizes)
    total_sizes = [max(1, s * max(1, iter_mult)) for s in sizes]

    # Optional warmup at median size
    if warmup_items > 0:
        mid = total_sizes[len(total_sizes) // 2]
        run_once(
            mid,
            args.providers,
            args.mode,
            seed,
            args.hotset,
            args.hotset_share,
            args.simulate_write,
        )

    if not args.no_header and not args.json:
        print(
            "batch      providers  mode   hotset%  share%  groups   elapsed_s    items/s      ns/item  peak_mem_MB   B/item"
        )

    for n in total_sizes:
        elapsed, peak_bytes, groups = run_once(
            n_items=n,
            providers=args.providers,
            mode=args.mode,
            seed=seed,
            hotset_frac=args.hotset,
            hotset_share=args.hotset_share,
            simulate_write=args.simulate_write,
        )
        items_per_s = n / elapsed if elapsed > 0 else float("inf")
        ns_per = (elapsed / n) * 1e9 if n > 0 else float("inf")
        b_per_item = (peak_bytes / n) if n > 0 else float("inf")

        if args.json:
            import json

            print(
                json.dumps(
                    {
                        "batch": n,
                        "providers": args.providers,
                        "mode": args.mode,
                        "hotset_frac": args.hotset,
                        "hotset_share": args.hotset_share,
                        "groups": groups,
                        "elapsed_s": elapsed,
                        "items_per_s": items_per_s,
                        "ns_per_item": ns_per,
                        "peak_mem_bytes": peak_bytes,
                        "bytes_per_item": b_per_item,
                        "seed": seed,
                        "simulate_write": args.simulate_write,
                    }
                )
            )
        else:
            print(
                f"{n:10d}  {args.providers:9d}  {args.mode:5s}  "
                f"{args.hotset*100:7.2f}  {args.hotset_share*100:6.2f}  "
                f"{groups:6d}  {elapsed:10.6f}  {items_per_s:10.0f}  {ns_per:11.1f}  "
                f"{human_mb(peak_bytes):11.2f}  {b_per_item:8.1f}"
            )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
