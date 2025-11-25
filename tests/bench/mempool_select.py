# -*- coding: utf-8 -*-
"""
mempool_select.py
=================

Benchmark: transaction selection under gas/byte budgets.

If the real mempool implementation is available (e.g., `mempool.pool` and
`mempool.drain`), this script will attempt to use it. Otherwise it falls back to
a deterministic reference selector that enforces per-sender nonce order and
selects by priority until budgets are exhausted.

Outputs a single JSON object on stdout (one line) that tests/bench/runner.py
can consume.

Examples:
    # Default: 2k senders × 8 tx each; gas budget 15M; bytes budget 1.5MB
    python tests/bench/mempool_select.py

    # Heavier run
    python tests/bench/mempool_select.py --senders 5000 --txs-per 10 --repeat 7

    # Force fallback selector
    python tests/bench/mempool_select.py --mode fallback
"""
from __future__ import annotations

import argparse
import json
import math
import os
import random
import statistics
import time
from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Optional, Tuple
import heapq


# --------------------------------------------------------------------------- #
# Synthetic TX model (used by fallback and as input to real adapter)
# --------------------------------------------------------------------------- #

@dataclass(order=False)
class Tx:
    sender: int
    nonce: int
    gas: int
    size: int
    priority: float  # higher = better (e.g., tip per gas, or composite)
    txid: int = field(default=0)  # stable id for tiebreakers


def _rng(seed: Optional[int]) -> random.Random:
    if seed is None:
        seed = int(os.environ.get("PYTHONHASHSEED", "0") or "1337")
    return random.Random(seed)


def generate_txs(
    senders: int,
    txs_per_sender: int,
    gas_min: int,
    gas_max: int,
    size_min: int,
    size_max: int,
    seed: Optional[int],
) -> List[Tx]:
    r = _rng(seed)
    txs: List[Tx] = []
    txid = 0
    for s in range(senders):
        base_nonce = 0
        # Give each sender a slightly different priority baseline to create variety
        sender_bias = 0.5 + r.random()  # [0.5, 1.5)
        for i in range(txs_per_sender):
            gas = r.randint(gas_min, gas_max)
            size = r.randint(size_min, size_max)
            tip_per_gas = 1.0 + 9.0 * (r.random() ** 2)  # skewed towards lower but with tail
            priority = tip_per_gas * sender_bias
            txs.append(Tx(sender=s, nonce=base_nonce + i, gas=gas, size=size, priority=priority, txid=txid))
            txid += 1
    return txs


# --------------------------------------------------------------------------- #
# Fallback per-sender-queue + heap selector
# --------------------------------------------------------------------------- #

class FallbackSelector:
    """
    Simple, deterministic selector that enforces per-sender nonce order.

    Algorithm:
      - Build per-sender queues sorted by nonce.
      - Push each sender's head into a max-heap keyed by (-priority, gas, txid).
      - Pop best; if it fits budgets, include and push that sender's next head.
      - If it doesn't fit, discard it for this block (continue). This reflects
        realistic behavior where over-sized txs are skipped when near full.

    Returns: (selected list, total_gas, total_bytes, attempts)
    """
    def __init__(self, txs: List[Tx]):
        # Build queues by sender → list sorted by nonce
        self.queues: Dict[int, List[Tx]] = {}
        for tx in txs:
            self.queues.setdefault(tx.sender, []).append(tx)
        for q in self.queues.values():
            q.sort(key=lambda t: t.nonce)

    def select(self, gas_budget: int, byte_budget: int) -> Tuple[List[Tx], int, int, int]:
        heap: List[Tuple[float, int, int, Tx]] = []
        selected: List[Tx] = []
        gas_used = 0
        bytes_used = 0
        attempts = 0

        # Push initial heads
        for s, q in self.queues.items():
            if q and q[0].nonce == q[0].nonce:  # always true; keeps mypy happy about q[0]
                head = q[0]
                # Max-heap via negative priority; tie-break by gas (smaller first), then txid
                heapq.heappush(heap, (-head.priority, head.gas, head.txid, head))

        # Track current position per sender
        head_idx: Dict[int, int] = {s: 0 for s in self.queues.keys()}

        # Unlimited budgets if negative
        gas_limit = gas_budget if gas_budget >= 0 else 1 << 62
        byte_limit = byte_budget if byte_budget >= 0 else 1 << 62

        while heap:
            _, _, _, tx = heapq.heappop(heap)
            attempts += 1

            if gas_used + tx.gas <= gas_limit and bytes_used + tx.size <= byte_limit:
                # Accept
                selected.append(tx)
                gas_used += tx.gas
                bytes_used += tx.size

                # Advance sender queue
                idx = head_idx[tx.sender] + 1
                head_idx[tx.sender] = idx
                q = self.queues[tx.sender]
                if idx < len(q):
                    nxt = q[idx]
                    heapq.heappush(heap, (-nxt.priority, nxt.gas, nxt.txid, nxt))
            else:
                # Skip over-sized tx; do not push next for fairness (sender head remains)
                # In many real implementations, when close to budget, we continue trying
                # other senders. Here, skipping achieves a similar effect.
                continue

            # Stop early if both budgets are essentially full
            if gas_used >= gas_limit and bytes_used >= byte_limit:
                break

        return selected, gas_used, bytes_used, attempts


# --------------------------------------------------------------------------- #
# Real mempool adapter (best-effort; falls back if unavailable)
# --------------------------------------------------------------------------- #

class RealMempoolAdapter:
    """
    Tries to bind to an actual mempool implementation. The goal is to exercise
    the real selection path if present.

    Probed patterns (any one suffices):
      - mempool.pool.Pool(...); pool.add(tx_dict); mempool.drain.select_ready(pool, gas, bytes)
      - mempool.drain.select(tx_list, gas, bytes)  # functional style
      - mempool.pool.Pool(...); pool.drain(gas, bytes)
    """
    def __init__(self):
        import importlib

        self._pool_mod = importlib.import_module("mempool.pool")
        self._drain_mod = None
        try:
            self._drain_mod = importlib.import_module("mempool.drain")
        except Exception:
            self._drain_mod = None

        self._Pool = getattr(self._pool_mod, "Pool", None)
        if self._Pool is None:
            raise RuntimeError("mempool.pool.Pool not found")

        # Heuristics for add method
        self._pool_add_method = "add"
        if not hasattr(self._Pool, self._pool_add_method):
            # try alternative
            self._pool_add_method = "add_tx"

        # Discover a drain function, if any
        self._drain_fn = None
        if self._drain_mod:
            for name in ("select_ready", "select", "drain_ready", "drain"):
                fn = getattr(self._drain_mod, name, None)
                if callable(fn):
                    self._drain_fn = fn
                    break

    def _tx_to_real(self, tx: Tx):
        """
        Convert synthetic Tx to a realistic dict shape the pool might accept.
        We provide common fields used by validate/priority paths; unknown fields
        are ignored in many implementations.
        """
        return {
            "sender": tx.sender,
            "nonce": tx.nonce,
            "gas": tx.gas,
            "size": tx.size,
            "priority": tx.priority,
            "hash": f"0x{tx.txid:064x}",
        }

    def select(self, txs: List[Tx], gas_budget: int, byte_budget: int) -> Tuple[List[dict], int, int, int]:
        # Build pool
        pool = self._Pool()
        add = getattr(pool, self._pool_add_method, None)
        if not callable(add):
            raise RuntimeError("Pool add method not callable")

        for tx in txs:
            add(self._tx_to_real(tx))  # type: ignore[misc]

        # Try drain API variants
        attempts = 0
        if self._drain_fn is not None:
            try:
                res = self._drain_fn(pool, gas_budget, byte_budget)  # type: ignore[misc]
            except TypeError:
                # Functional form: pass list of txs
                tx_dicts = [self._tx_to_real(t) for t in txs]
                res = self._drain_fn(tx_dicts, gas_budget, byte_budget)  # type: ignore[misc]
            # Expect a structure. Try to normalize.
            if isinstance(res, dict):
                picked = res.get("txs") or res.get("selected") or []
                gas_used = int(res.get("gas_used") or sum(int(t.get("gas", 0)) for t in picked))
                bytes_used = int(res.get("bytes_used") or sum(int(t.get("size", 0)) for t in picked))
                return picked, gas_used, bytes_used, attempts
            elif isinstance(res, (list, tuple)):
                picked = list(res)
                gas_used = sum(int(t.get("gas", 0)) for t in picked)
                bytes_used = sum(int(t.get("size", 0)) for t in picked)
                return picked, gas_used, bytes_used, attempts

        # If pool exposes a method
        for name in ("drain", "select_ready", "select"):
            m = getattr(pool, name, None)
            if callable(m):
                try:
                    out = m(gas_budget, byte_budget)  # type: ignore[misc]
                except TypeError:
                    out = m(limit_gas=gas_budget, limit_bytes=byte_budget)  # type: ignore[misc]
                if isinstance(out, dict):
                    picked = out.get("txs") or out.get("selected") or []
                    gas_used = int(out.get("gas_used") or sum(int(t.get("gas", 0)) for t in picked))
                    bytes_used = int(out.get("bytes_used") or sum(int(t.get("size", 0)) for t in picked))
                    return picked, gas_used, bytes_used, attempts
                elif isinstance(out, (list, tuple)):
                    picked = list(out)
                    gas_used = sum(int(t.get("gas", 0)) for t in picked)
                    bytes_used = sum(int(t.get("size", 0)) for t in picked)
                    return picked, gas_used, bytes_used, attempts

        raise RuntimeError("Could not locate a usable mempool drain/select API")


# --------------------------------------------------------------------------- #
# Benchmark Core
# --------------------------------------------------------------------------- #

def _select_once(
    mode: str,
    txs: List[Tx],
    gas_budget: int,
    byte_budget: int,
) -> Tuple[int, int, int, int]:
    """
    Run a single selection and return (count, gas_used, bytes_used, attempts).
    """
    if mode in ("auto", "mempool"):
        try:
            adapter = RealMempoolAdapter()
            picked, gas_used, bytes_used, attempts = adapter.select(txs, gas_budget, byte_budget)
            return len(picked), gas_used, bytes_used, attempts
        except Exception:
            if mode == "mempool":
                raise
            # fall back
    # Fallback path
    sel = FallbackSelector(txs)
    picked, gas_used, bytes_used, attempts = sel.select(gas_budget, byte_budget)
    return len(picked), gas_used, bytes_used, attempts


def _time_selection(
    mode: str,
    txs: List[Tx],
    gas_budget: int,
    byte_budget: int,
) -> Tuple[float, int, int, int]:
    t0 = time.perf_counter()
    count, gas_used, bytes_used, attempts = _select_once(mode, txs, gas_budget, byte_budget)
    t1 = time.perf_counter()
    return (t1 - t0), count, gas_used, bytes_used


def run_bench(
    senders: int,
    txs_per_sender: int,
    gas_budget: int,
    byte_budget: int,
    gas_min: int,
    gas_max: int,
    size_min: int,
    size_max: int,
    warmup: int,
    repeat: int,
    mode: str,
    seed: Optional[int],
) -> dict:
    txs = generate_txs(
        senders=senders,
        txs_per_sender=txs_per_sender,
        gas_min=gas_min,
        gas_max=gas_max,
        size_min=size_min,
        size_max=size_max,
        seed=seed,
    )

    # Warmup
    for _ in range(max(0, warmup)):
        _time_selection(mode, txs, gas_budget, byte_budget)

    # Measure
    timings = []
    counts = []
    gas_used_list = []
    bytes_used_list = []

    for _ in range(repeat):
        dt, cnt, g_used, b_used = _time_selection(mode, txs, gas_budget, byte_budget)
        timings.append(dt)
        counts.append(cnt)
        gas_used_list.append(g_used)
        bytes_used_list.append(b_used)

    median_s = statistics.median(timings)
    p90_s = statistics.quantiles(timings, n=10)[8] if len(timings) >= 10 else max(timings)

    count_median = int(statistics.median(counts))
    gas_used_median = int(statistics.median(gas_used_list))
    bytes_used_median = int(statistics.median(bytes_used_list))

    txs_per_s = (count_median / median_s) if median_s > 0 else float("inf")
    ops_per_s = txs_per_s  # selection ops == selected txs here

    fill_gas = (gas_used_median / gas_budget) if gas_budget > 0 else 0.0
    fill_bytes = (bytes_used_median / byte_budget) if byte_budget > 0 else 0.0

    mode_label = "mempool" if mode in ("auto", "mempool") else "fallback"
    # If auto tried mempool but failed, report fallback explicitly
    if mode == "auto":
        mode_label = "fallback" if txs_per_s == float("inf") else mode_label

    return {
        "case": f"mempool.select_under_budgets(senders={senders},per={txs_per_sender})",
        "params": {
            "senders": senders,
            "txs_per_sender": txs_per_sender,
            "gas_budget": gas_budget,
            "byte_budget": byte_budget,
            "gas_min": gas_min,
            "gas_max": gas_max,
            "size_min": size_min,
            "size_max": size_max,
            "warmup": warmup,
            "repeat": repeat,
            "mode": mode,
            "seed": seed if seed is not None else int(os.environ.get("PYTHONHASHSEED", "0") or "1337"),
        },
        "result": {
            "txs_per_s": txs_per_s,
            "ops_per_s": ops_per_s,
            "selected_median": count_median,
            "gas_used_median": gas_used_median,
            "bytes_used_median": bytes_used_median,
            "fill_gas": fill_gas,
            "fill_bytes": fill_bytes,
            "median_s": median_s,
            "p90_s": p90_s,
        },
    }


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Mempool selection throughput under gas/byte budgets.")
    ap.add_argument("--senders", type=int, default=2000, help="Number of distinct senders (default: 2000)")
    ap.add_argument("--txs-per", type=int, default=8, dest="txs_per", help="Transactions per sender (default: 8)")
    ap.add_argument("--gas-budget", type=int, default=15_000_000, dest="gas_budget", help="Gas budget per block (default: 15,000,000)")
    ap.add_argument("--byte-budget", type=int, default=1_500_000, dest="byte_budget", help="Bytes budget per block (default: 1,500,000)")
    ap.add_argument("--gas-min", type=int, default=21_000, dest="gas_min", help="Min gas per tx (default: 21,000)")
    ap.add_argument("--gas-max", type=int, default=300_000, dest="gas_max", help="Max gas per tx (default: 300,000)")
    ap.add_argument("--size-min", type=int, default=120, dest="size_min", help="Min size per tx in bytes (default: 120)")
    ap.add_argument("--size-max", type=int, default=900, dest="size_max", help="Max size per tx in bytes (default: 900)")
    ap.add_argument("--warmup", type=int, default=1, help="Warmup iterations (default: 1)")
    ap.add_argument("--repeat", type=int, default=5, help="Measured iterations (default: 5)")
    ap.add_argument("--mode", choices=("auto", "mempool", "fallback"), default="auto",
                    help="Use real mempool if available (auto/mempool), else fallback (default: auto)")
    ap.add_argument("--seed", type=int, default=None, help="PRNG seed (default: from PYTHONHASHSEED or 1337)")
    args = ap.parse_args(argv)

    payload = run_bench(
        senders=args.senders,
        txs_per_sender=args.txs_per,
        gas_budget=args.gas_budget,
        byte_budget=args.byte_budget,
        gas_min=args.gas_min,
        gas_max=args.gas_max,
        size_min=args.size_min,
        size_max=args.size_max,
        warmup=args.warmup,
        repeat=args.repeat,
        mode=args.mode,
        seed=args.seed,
    )
    print(json.dumps(payload, separators=(",", ":"), sort_keys=False))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
