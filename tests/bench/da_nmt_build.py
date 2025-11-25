# -*- coding: utf-8 -*-
"""
da_nmt_build.py
================

Namespaced Merkle Tree (NMT) build throughput.

Measures how fast we can construct an NMT root from a batch of (namespace, data)
leaves. If a real implementation from `da.nmt` is available, we will use it.
Otherwise we fall back to a simple, deterministic reference that:
  - hashes leaves as H(b"NMT\x00" || uvarint(ns) || data)
  - hashes internal nodes as H(b"NMT\x01" || left || right)
  - sorts leaves by (namespace, original_index)
  - duplicates the last item when the level width is odd

Output: prints one JSON object (single line) consumable by tests/bench/runner.py.

Examples:
    # Default: 65_536 leaves, 64B each, 5 rounds, 5 repeats
    python tests/bench/da_nmt_build.py

    # Heavier run
    python tests/bench/da_nmt_build.py --leaves 200000 --leaf-size 128 --rounds 10 --repeat 7

    # Force fallback
    python tests/bench/da_nmt_build.py --mode fallback
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import random
import statistics
import time
from dataclasses import dataclass
from typing import Callable, Iterable, List, Optional, Sequence, Tuple

# ------------------------------ RNG & helpers ---------------------------------

def _rng(seed: Optional[int]) -> random.Random:
    if seed is None:
        seed = int(os.environ.get("PYTHONHASHSEED", "0") or "1337")
    return random.Random(seed)

def _rand_bytes(r: random.Random, n: int) -> bytes:
    return bytes(r.getrandbits(8) for _ in range(n))

def _to_hex(b: bytes) -> str:
    return "0x" + b.hex()

# Minimal unsigned varint encoder (LEB128-like; 7-bit chunks)
def _uvarint(x: int) -> bytes:
    if x < 0:
        raise ValueError("uvarint expects non-negative integer")
    out = bytearray()
    while True:
        b = x & 0x7F
        x >>= 7
        if x:
            out.append(b | 0x80)
        else:
            out.append(b)
            break
    return bytes(out)

# ------------------------------ Adapters --------------------------------------

@dataclass
class Leaf:
    ns: int
    data: bytes

class NMTAdapter:
    """Abstract adapter: compute root for a batch of leaves."""
    def __init__(self, ns_bits: int):
        self.ns_bits = ns_bits

    def root(self, leaves: Sequence[Leaf]) -> bytes:
        raise NotImplementedError

class FallbackNMT(NMTAdapter):
    """
    Deterministic, simple NMT-like hasher with namespace-aware leaf hashing
    and naive binary Merkle construction. Not a full NMT proof system—just a
    stable, comparable root for benchmarks when real DA code isn't present.
    """

    def __init__(self, ns_bits: int):
        super().__init__(ns_bits)
        self._h_leaf_tag = b"NMT\x00"
        self._h_node_tag = b"NMT\x01"

    def _leaf_hash(self, ns: int, data: bytes) -> bytes:
        h = hashlib.sha3_256()
        h.update(self._h_leaf_tag)
        h.update(_uvarint(ns))
        h.update(data)
        return h.digest()

    def _node_hash(self, left: bytes, right: bytes) -> bytes:
        h = hashlib.sha3_256()
        h.update(self._h_node_tag)
        h.update(left)
        h.update(right)
        return h.digest()

    def root(self, leaves: Sequence[Leaf]) -> bytes:
        if not leaves:
            # Hash empty tree deterministically
            return hashlib.sha3_256(b"NMT\xffEMPTY").digest()

        # Sort by (namespace, input index) to mimic NMT range ordering
        indexed = list(enumerate(leaves))
        indexed.sort(key=lambda t: (t[1].ns, t[0]))
        level = [self._leaf_hash(l.ns, l.data) for _, l in indexed]

        # Pairwise up the tree; duplicate last if odd
        while len(level) > 1:
            nxt: List[bytes] = []
            it = iter(level)
            for a in it:
                try:
                    b = next(it)
                except StopIteration:
                    b = a
                nxt.append(self._node_hash(a, b))
            level = nxt
        return level[0]

def _maybe_real_adapter(ns_bits: int) -> Optional[NMTAdapter]:
    """
    Try to bind to a real implementation if present.

    Supported patterns:
      - da.nmt.commit.commit(leaves)  where leaves ~ [(ns:int, data:bytes), ...] or encoded bytes
      - da.nmt.tree.Tree(ns_bits).append(ns, data); .finalize_root()
      - da.nmt.commit.commit_encoded(encoded_leaves) via da.nmt.codec.encode_leaf(ns, data)
    """
    try:
        import importlib

        # Try commit API first
        commit_mod = importlib.import_module("da.nmt.commit")
        commit_fn = getattr(commit_mod, "commit", None)

        leaf_encoder = None
        try:
            codec_mod = importlib.import_module("da.nmt.codec")
            # Accept a few likely names
            for name in ("encode_leaf", "leaf_encode", "encode"):
                f = getattr(codec_mod, name, None)
                if callable(f):
                    leaf_encoder = f
                    break
        except Exception:
            leaf_encoder = None

        if callable(commit_fn):
            class _CommitAdapter(NMTAdapter):
                def root(self, leaves: Sequence[Leaf]) -> bytes:
                    # Try several calling conventions
                    # 1) Raw (ns, data) tuples
                    try:
                        tup_leaves = [(l.ns, l.data) for l in leaves]
                        root = commit_fn(tup_leaves)  # type: ignore[misc]
                        if isinstance(root, (bytes, bytearray)):
                            return bytes(root)
                        # maybe returns (root, meta)
                        if isinstance(root, (tuple, list)) and root:
                            r0 = root[0]
                            if isinstance(r0, (bytes, bytearray)):
                                return bytes(r0)
                    except Exception:
                        pass

                    # 2) Encoded leaves via codec
                    if leaf_encoder is not None:
                        enc = [leaf_encoder(l.ns, l.data) for l in leaves]  # type: ignore[misc]
                        try:
                            root = commit_fn(enc)  # type: ignore[misc]
                            if isinstance(root, (bytes, bytearray)):
                                return bytes(root)
                            if isinstance(root, (tuple, list)) and root:
                                r0 = root[0]
                                if isinstance(r0, (bytes, bytearray)):
                                    return bytes(r0)
                        except Exception:
                            pass

                    # 3) Dict leaves
                    try:
                        dict_leaves = [{"ns": l.ns, "data": l.data} for l in leaves]
                        root = commit_fn(dict_leaves)  # type: ignore[misc]
                        if isinstance(root, (bytes, bytearray)):
                            return bytes(root)
                        if isinstance(root, (tuple, list)) and root:
                            r0 = root[0]
                            if isinstance(r0, (bytes, bytearray)):
                                return bytes(r0)
                    except Exception:
                        pass

                    raise RuntimeError("Unable to use da.nmt.commit.commit with provided leaves")
            return _CommitAdapter(ns_bits)
    except Exception:
        # ignore and try tree API
        pass

    try:
        import importlib
        tree_mod = importlib.import_module("da.nmt.tree")
        TreeCls = getattr(tree_mod, "Tree", None)
        if TreeCls is not None:
            class _TreeAdapter(NMTAdapter):
                def root(self, leaves: Sequence[Leaf]) -> bytes:
                    t = TreeCls(ns_bits)  # type: ignore
                    for l in leaves:
                        # Support either t.append(ns, data) or t.append((ns, data))
                        try:
                            t.append(l.ns, l.data)  # type: ignore
                        except TypeError:
                            t.append((l.ns, l.data))  # type: ignore
                    # Prefer explicit finalize/root names
                    for meth in ("finalize_root", "root", "finalize", "get_root"):
                        fn = getattr(t, meth, None)
                        if callable(fn):
                            r = fn()
                            if isinstance(r, (bytes, bytearray)):
                                return bytes(r)
                    # If none available, try attribute
                    r = getattr(t, "root", None)
                    if isinstance(r, (bytes, bytearray)):
                        return bytes(r)
                    raise RuntimeError("Tree adapter could not obtain root")
            return _TreeAdapter(ns_bits)
    except Exception:
        pass

    return None

# ------------------------------ Benchmark core --------------------------------

def _timeit(fn: Callable[[], bytes], repeats: int) -> Tuple[List[float], bytes]:
    timings: List[float] = []
    last = b""
    for _ in range(repeats):
        t0 = time.perf_counter()
        last = fn()
        t1 = time.perf_counter()
        timings.append(t1 - t0)
    return timings, last

def _gen_leaves(n: int, leaf_size: int, ns_bits: int, seed: Optional[int]) -> List[Leaf]:
    r = _rng(seed)
    ns_mask = (1 << ns_bits) - 1
    leaves: List[Leaf] = []
    for _ in range(n):
        ns = r.getrandbits(ns_bits) & ns_mask
        data = _rand_bytes(r, leaf_size)
        leaves.append(Leaf(ns=ns, data=data))
    return leaves

def run_bench(
    leaves: int,
    leaf_size: int,
    rounds: int,
    warmup: int,
    repeat: int,
    ns_bits: int,
    mode: str,
    seed: Optional[int],
) -> dict:
    # Prepare one data batch; we only time the root computation.
    dataset = _gen_leaves(leaves, leaf_size, ns_bits, seed)

    # Adapter selection
    adapter = None
    if mode in ("auto", "da"):
        adapter = _maybe_real_adapter(ns_bits)
    if adapter is None:
        adapter = FallbackNMT(ns_bits)
        label = "fallback"
    else:
        label = "da"

    # Workload: build 'rounds' trees per iteration over the same leaves
    def do_build() -> bytes:
        root = b""
        for _ in range(rounds):
            root = adapter.root(dataset)
        return root

    # Warmup
    if warmup > 0:
        _timeit(do_build, warmup)

    # Measure
    timings, last_root = _timeit(do_build, repeat)

    build_median = statistics.median(timings)
    build_p90 = statistics.quantiles(timings, n=10)[8] if len(timings) >= 10 else max(timings)

    total_leaves = leaves * rounds
    leaves_per_s = (total_leaves / build_median) if build_median > 0 else float("inf")

    return {
        "case": f"da.nmt_build(leaves={leaves},size={leaf_size})",
        "params": {
            "leaves": leaves,
            "leaf_size": leaf_size,
            "rounds": rounds,
            "repeat": repeat,
            "warmup": warmup,
            "ns_bits": ns_bits,
            "mode": label,
            "seed": seed if seed is not None else int(os.environ.get("PYTHONHASHSEED", "0") or "1337"),
        },
        "result": {
            "leaves_per_s": leaves_per_s,
            "build_median_s": build_median,
            "build_p90_s": build_p90,
            "root_hex": _to_hex(last_root),
        },
    }

def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="NMT build throughput benchmark.")
    ap.add_argument("--leaves", type=int, default=65_536, help="Number of leaves per build (default: 65536)")
    ap.add_argument("--leaf-size", type=int, default=64, dest="leaf_size", help="Leaf payload size in bytes (default: 64)")
    ap.add_argument("--rounds", type=int, default=5, help="Number of tree builds per iteration (default: 5)")
    ap.add_argument("--warmup", type=int, default=1, help="Warmup iterations (default: 1)")
    ap.add_argument("--repeat", type=int, default=5, help="Measured iterations (default: 5)")
    ap.add_argument("--ns-bits", type=int, default=16, dest="ns_bits", help="Namespace id bit width (default: 16)")
    ap.add_argument("--mode", choices=("auto", "da", "fallback"), default="auto",
                    help="Use real da.nmt implementation if available (default: auto)")
    ap.add_argument("--seed", type=int, default=None, help="PRNG seed (default: from PYTHONHASHSEED or 1337)")
    args = ap.parse_args(argv)

    payload = run_bench(
        leaves=args.leaves,
        leaf_size=args.leaf_size,
        rounds=args.rounds,
        warmup=args.warmup,
        repeat=args.repeat,
        ns_bits=args.ns_bits,
        mode=args.mode,
        seed=args.seed,
    )

    print(json.dumps(payload, separators=(",", ":"), sort_keys=False))
    return 0

if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
