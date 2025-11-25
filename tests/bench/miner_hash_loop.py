# -*- coding: utf-8 -*-
"""
miner_hash_loop.py
==================

Benchmark the CPU inner hash loop used for HashShare-style mining.
Measures hashes/sec for a deterministic single-thread loop and (optionally)
counts "shares" using a simple dev difficulty modeled as N leading zero bits.

If a faster keccak256 implementation is present (e.g., from pycryptodomex),
the script will use it; otherwise it falls back to hashlib.sha3_256.

Outputs a single JSON object (one line) usable by tests/bench/runner.py.

Examples:
    python tests/bench/miner_hash_loop.py
    python tests/bench/miner_hash_loop.py --seconds 5 --target-bits 18
    python tests/bench/miner_hash_loop.py --alg keccak_256
"""
from __future__ import annotations

import argparse
import json
import os
import struct
import time
from typing import Callable, Optional

# -----------------------------------------------------------------------------
# Hash providers (auto → keccak_256 if available, else sha3_256)
# -----------------------------------------------------------------------------

def _get_hash_fn(alg_choice: str) -> tuple[str, Callable[[bytes], bytes]]:
    """
    Returns (alg_label, hash_func) where hash_func(b: bytes)-> digest bytes.
    """
    alg_choice = alg_choice.lower()
    if alg_choice not in ("auto", "keccak_256", "sha3_256"):
        raise ValueError("Unsupported alg (choose auto|keccak_256|sha3_256)")

    # Try keccak_256 first if auto or explicitly requested
    if alg_choice in ("auto", "keccak_256"):
        # pycryptodomex
        try:
            from Crypto.Hash import keccak  # type: ignore
            def _k(b: bytes) -> bytes:
                h = keccak.new(digest_bits=256)
                h.update(b)
                return h.digest()
            return "keccak_256", _k
        except Exception:
            # eth_hash (py-evm stack)
            try:
                from eth_hash.auto import keccak as eth_keccak  # type: ignore
                def _ek(b: bytes) -> bytes:
                    return eth_keccak(b)
                return "keccak_256", _ek
            except Exception:
                if alg_choice == "keccak_256":
                    raise

    # Fallback: stdlib SHA3-256 (NIST padding; fine for a perf counter)
    import hashlib
    def _sha3(b: bytes) -> bytes:
        h = hashlib.sha3_256()
        h.update(b)
        return h.digest()
    return "sha3_256", _sha3


# -----------------------------------------------------------------------------
# Utilities
# -----------------------------------------------------------------------------

def _target_from_zero_bits(zero_bits: int) -> int:
    """
    Convert a leading-zero-bits threshold into a 256-bit integer target.
    Accept if int(digest) < target.
    """
    if zero_bits <= 0:
        return (1 << 256) - 1  # accept everything
    if zero_bits >= 256:
        return 0  # accept nothing (practically)
    return 1 << (256 - zero_bits)


def _int256(b: bytes) -> int:
    return int.from_bytes(b, "big", signed=False)


# -----------------------------------------------------------------------------
# Core bench
# -----------------------------------------------------------------------------

def run_hash_loop(
    seconds: float,
    target_bits: int,
    prefix_len: int,
    seed: Optional[int],
    alg_choice: str,
) -> dict:
    """
    Run a tight (single-thread) loop hashing prefix||nonce and counting attempts.

    prefix: deterministic bytes of length prefix_len
    nonce: 8-byte little-endian counter appended to prefix
    """
    # Deterministic prefix (avoid os.urandom for reproducibility)
    # Simple LCG for bytes if seed provided; else fixed pattern.
    if seed is None:
        prefix = (b"ANIMICA-DEV-PREFIX-" * ((prefix_len + 18) // 18))[:prefix_len]
    else:
        # Light-weight LCG
        a = 6364136223846793005
        c = 1442695040888963407
        x = (seed & ((1 << 64) - 1)) or 1
        out = bytearray(prefix_len)
        for i in range(prefix_len):
            x = (a * x + c) & ((1 << 64) - 1)
            out[i] = (x >> 32) & 0xFF
        prefix = bytes(out)

    alg_label, hfn = _get_hash_fn(alg_choice)
    target = _target_from_zero_bits(target_bits)

    # Pre-bind locals for speed in the inner loop
    pack_u64 = struct.Struct("<Q").pack
    digest_int = _int256
    threshold = target
    prefix_bytes = prefix
    hash_func = hfn

    attempts = 0
    shares = 0
    nonce = 0

    t0 = time.perf_counter()
    deadline = t0 + float(max(0.01, seconds))

    # Inner loop
    while True:
        # Unroll a bit for speed without complicating readability
        for _ in range(8):
            nonce_bytes = pack_u64(nonce)
            d = hash_func(prefix_bytes + nonce_bytes)
            attempts += 1
            if digest_int(d) < threshold:
                shares += 1
            nonce += 1
        if time.perf_counter() >= deadline:
            break

    t1 = time.perf_counter()
    elapsed = max(1e-9, t1 - t0)
    hashes_per_s = attempts / elapsed

    # Expected share rate given target_bits
    # p = 2^(-target_bits) for uniform 256-bit outputs.
    expected_p = 0.0 if target_bits >= 256 else (1.0 / (2.0 ** target_bits))
    expected_shares = attempts * expected_p

    return {
        "case": "miner.hash_loop",
        "params": {
            "seconds": seconds,
            "target_bits": target_bits,
            "prefix_len": prefix_len,
            "seed": seed,
            "alg": alg_label,
        },
        "result": {
            "attempts": attempts,
            "elapsed_s": elapsed,
            "hashes_per_s": hashes_per_s,
            "shares_found": shares,
            "expected_shares": expected_shares,
            "share_prob": expected_p,
        },
    }


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="CPU inner loop hashes/sec benchmark (dev Θ model via leading-zero bits).")
    ap.add_argument("--seconds", type=float, default=3.0, help="Benchmark duration in seconds (default: 3.0)")
    ap.add_argument("--target-bits", type=int, default=16,
                    help="Leading zero bits required to count as a 'share' (dev difficulty; default: 16)")
    ap.add_argument("--prefix-len", type=int, default=80, help="Prefix bytes length to hash with nonce (default: 80)")
    ap.add_argument("--seed", type=int, default=None, help="Deterministic prefix seed (default: fixed pattern)")
    ap.add_argument("--alg", choices=("auto", "keccak_256", "sha3_256"), default="auto",
                    help="Hash algorithm to use (default: auto)")
    args = ap.parse_args(argv)

    payload = run_hash_loop(
        seconds=args.seconds,
        target_bits=args.target_bits,
        prefix_len=args.prefix_len,
        seed=args.seed,
        alg_choice=args.alg,
    )
    print(json.dumps(payload, separators=(",", ":"), sort_keys=False))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
