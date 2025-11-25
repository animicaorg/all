#!/usr/bin/env python3
"""
NMT build throughput benchmark.

Generates a batch of random namespaced leaves and measures the time to
build/finalize a Namespaced Merkle Tree (NMT). The script is adapter-agnostic:
it will try multiple likely builder/commit APIs found under da.nmt.tree and
da.nmt.commit.

Usage (examples):
  python -m da.bench.nmt_build
  python -m da.bench.nmt_build --leaves 65536 --min-bytes 128 --max-bytes 512
  python -m da.bench.nmt_build --rounds 3 --ns-bits 16 --seed 42
"""

from __future__ import annotations

import argparse
import os
import random
import statistics
import time
from typing import Any, Callable, Iterable, List, Optional, Sequence, Tuple

# -----------------------------
# Helpers to locate NMT builder
# -----------------------------

def _import(mod: str):
    return __import__(mod, fromlist=["*"])


def _get_leaf_encoder() -> Optional[Callable[[int, bytes], bytes]]:
    """Try to locate a leaf encoder in da.nmt.codec (optional)."""
    try:
        codec = _import("da.nmt.codec")
    except ModuleNotFoundError:
        return None

    for name in ("encode_leaf", "encode", "encode_leaf_bytes", "leaf_encode"):
        if hasattr(codec, name):
            fn = getattr(codec, name)
            return lambda ns, data: fn(ns, data)  # type: ignore[misc]
    return None


def _get_commit_fn() -> Optional[Callable[[Iterable[Any]], bytes]]:
    """Try to locate a direct 'compute root from leaves' function."""
    try:
        commit = _import("da.nmt.commit")
    except ModuleNotFoundError:
        return None

    for name in ("commit_nmt_root", "compute_root", "root", "commit_root", "compute"):
        if hasattr(commit, name):
            return getattr(commit, name)
    return None


def _new_tree_builder() -> Tuple[str, Callable[[], Any], Callable[[Any, Any], None], Callable[[Any], bytes]]:
    """
    Returns:
      - label for the detected API
      - ctor(): -> builder
      - append(builder, leaf_or_pair)
      - finalize(builder) -> root bytes
    """
    tree_mod = _import("da.nmt.tree")  # will raise if truly missing

    # Class-based builders
    for cls_name in ("NMT", "NamespacedMerkleTree", "Tree", "Builder", "NMTBuilder"):
        if hasattr(tree_mod, cls_name):
            C = getattr(tree_mod, cls_name)
            # Probe method names
            append_name = None
            for nm in ("append", "push", "push_leaf", "add", "add_leaf"):
                if hasattr(C, nm):
                    append_name = nm
                    break
            fin_name = None
            for nm in ("finalize", "root", "build", "compute_root"):
                if hasattr(C, nm):
                    fin_name = nm
                    break
            if append_name and fin_name:
                def ctor() -> Any:
                    try:
                        return C()
                    except TypeError:
                        # Some implementations require params; try no-arg variant only
                        return C  # type: ignore[return-value]
                def append_fn(b, x):
                    getattr(b, append_name)(*x) if isinstance(x, tuple) else getattr(b, append_name)(x)
                def finalize_fn(b) -> bytes:
                    r = getattr(b, fin_name)()
                    return r if isinstance(r, (bytes, bytearray)) else bytes(r)
                return (f"{cls_name}.{append_name}/{fin_name}", ctor, append_fn, finalize_fn)

    # Module-level functions (build from leaves)
    for fn_name in ("build", "build_tree", "build_and_root", "compute_root_from_leaves"):
        if hasattr(tree_mod, fn_name):
            fn = getattr(tree_mod, fn_name)
            def ctor():  # stateless
                return fn
            def append_fn(_builder, _x):  # not used
                raise RuntimeError("append called on functional builder")
            def finalize_fn(fnlike) -> bytes:
                raise RuntimeError("finalize called on functional builder")
            # We'll detect functional builder in call site
            return (fn_name, ctor, append_fn, finalize_fn)

    raise RuntimeError("Could not locate a compatible NMT builder in da.nmt.tree")


# ---------------------------------
# Data generation & bench utilities
# ---------------------------------

def _gen_leaves(n: int, ns_bits: int, min_bytes: int, max_bytes: int, seed: int) -> Tuple[List[Tuple[int, bytes]], int]:
    rnd = random.Random(seed)
    ns_max = (1 << ns_bits) - 1
    leaves: List[Tuple[int, bytes]] = []
    total = 0
    for _ in range(n):
        ns = rnd.randint(0, ns_max)
        ln = rnd.randint(min_bytes, max_bytes)
        # Bias towards smaller payloads for a more realistic distribution
        if ln > min_bytes and rnd.random() < 0.6:
            ln = int(min_bytes + (ln - min_bytes) * rnd.random() ** 2)
        data = rnd.randbytes(ln) if hasattr(rnd, "randbytes") else bytes(rnd.getrandbits(8) for _ in range(ln))
        leaves.append((ns, data))
        total += ln
    return leaves, total


def _sizeof(data_bytes: int) -> str:
    return f"{data_bytes/1024/1024:.2f} MiB"


# -------------
# Main benchmark
# -------------

def run_round(leaves: Sequence[Tuple[int, bytes]],
              leaf_encoder: Optional[Callable[[int, bytes], bytes]],
              builder_pack: Tuple[str, Callable[[], Any], Callable[[Any, Any], None], Callable[[Any], bytes]]) -> Tuple[float, bytes]:
    label, ctor, append_fn, finalize_fn = builder_pack
    builder = ctor()

    # Functional builder form: ctor() returned a function that takes leaves and returns root
    if callable(builder) and not hasattr(builder, "__self__") and label in ("build", "build_tree", "build_and_root", "compute_root_from_leaves"):
        fn = builder  # type: ignore[assignment]
        t0 = time.perf_counter()
        root = fn(leaves)  # type: ignore[misc]
        dt = time.perf_counter() - t0
        root_bytes = root if isinstance(root, (bytes, bytearray)) else bytes(root)
        return dt, root_bytes

    # Class/instance builder form
    t0 = time.perf_counter()
    if leaf_encoder:
        for ns, data in leaves:
            encoded = leaf_encoder(ns, data)
            append_fn(builder, encoded)
    else:
        for ns, data in leaves:
            append_fn(builder, (ns, data))
    root = finalize_fn(builder)
    dt = time.perf_counter() - t0
    return dt, root


def main():
    p = argparse.ArgumentParser(description="NMT build throughput benchmark")
    p.add_argument("--leaves", type=int, default=32768, help="Number of leaves to build (default: 32768)")
    p.add_argument("--min-bytes", type=int, default=192, help="Minimum payload size per leaf")
    p.add_argument("--max-bytes", type=int, default=512, help="Maximum payload size per leaf")
    p.add_argument("--ns-bits", type=int, default=24, help="Namespace id bit width")
    p.add_argument("--rounds", type=int, default=3, help="Number of timed rounds (roots should match)")
    p.add_argument("--seed", type=int, default=0xDA5EED, help="PRNG seed for leaf generation")
    p.add_argument("--warmup", type=int, default=1, help="Warm-up iterations (not measured)")
    args = p.parse_args()

    try:
        label, ctor, append_fn, finalize_fn = _new_tree_builder()
    except Exception as e:
        raise SystemExit(f"[bench] Could not find an NMT builder: {e}")

    leaf_encoder = _get_leaf_encoder()
    commit_fn = _get_commit_fn()

    leaves, total_bytes = _gen_leaves(args.leaves, args.ns_bits, args.min_bytes, args.max_bytes, args.seed)
    print(f"[bench] N={args.leaves} leaves, payload ≈ {_sizeof(total_bytes)}, ns_bits={args.ns_bits}")
    print(f"[bench] Builder API: {label}{' + encoder' if leaf_encoder else ''}")

    # Optional: verify that repeated builds produce the same root (and match commit fn if available)
    # Warm-up
    for _ in range(args.warmup):
        _ = run_round(leaves, leaf_encoder, (label, ctor, append_fn, finalize_fn))

    times: List[float] = []
    last_root: Optional[bytes] = None

    for i in range(args.rounds):
        dt, root = run_round(leaves, leaf_encoder, (label, ctor, append_fn, finalize_fn))
        times.append(dt)
        if last_root is None:
            last_root = root
        else:
            if root != last_root:
                raise SystemExit("[bench] ERROR: root mismatch across rounds")
        mbps = (total_bytes / (1024 * 1024)) / dt if dt > 0 else float("inf")
        lps = args.leaves / dt if dt > 0 else float("inf")
        print(f"[round {i+1}/{args.rounds}] {dt*1000:.1f} ms  |  {mbps:8.2f} MiB/s  |  {lps:9.0f} leaves/s")

    if commit_fn is not None:
        try:
            # Try both tuple-leaves and pre-encoded leaves for commit fn
            ref = None
            try:
                ref = commit_fn(leaves)  # type: ignore[misc]
            except Exception:
                if leaf_encoder:
                    enc = [leaf_encoder(ns, data) for ns, data in leaves]
                    ref = commit_fn(enc)  # type: ignore[misc]
            if isinstance(ref, (bytes, bytearray)) and last_root is not None and bytes(ref) != last_root:
                print("[bench] WARNING: commit() root != builder root (APIs may differ on input form)")
        except Exception as e:
            print(f"[bench] NOTE: commit cross-check failed ({e!r})")

    mean = statistics.mean(times)
    p50 = statistics.median(times)
    p95 = statistics.quantiles(times, n=100)[94] if len(times) >= 2 else mean
    mbps = (total_bytes / (1024 * 1024)) / mean if mean > 0 else float("inf")
    lps = args.leaves / mean if mean > 0 else float("inf")

    print(
        "\n[summary] "
        f"mean={mean*1000:.1f} ms  p50={p50*1000:.1f} ms  p95={p95*1000:.1f} ms  "
        f"throughput={mbps:.2f} MiB/s, {lps:.0f} leaves/s"
    )
    if last_root is not None:
        print(f"[root] 0x{last_root.hex()}")

if __name__ == "__main__":
    main()
