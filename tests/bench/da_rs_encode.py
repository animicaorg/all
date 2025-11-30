# -*- coding: utf-8 -*-
"""
da_rs_encode.py
================

Reed–Solomon encode/decode throughput (MB/s).

This benchmark prefers a real implementation from `da.erasure.reedsolomon`
(and/or `da.erasure.encoder`) and falls back to a simple XOR-parity simulator
that supports recovery of a single missing shard. The intent is to provide a
stable perf signal for CI; if a production RS codec is available, its adapter
will be used automatically.

Output: prints a single JSON object (last line) suitable for tests/bench/runner.py.

Examples:
    # Default: k=128, n=256, shard=4096B, 200 rounds, 5 repeats
    python tests/bench/da_rs_encode.py

    # Heavier run
    python tests/bench/da_rs_encode.py --k 128 --n 256 --shard 8192 --rounds 400 --repeat 7

    # Force fallback simulator
    python tests/bench/da_rs_encode.py --mode fallback
"""
from __future__ import annotations

import argparse
import json
import os
import random
import statistics
import time
from typing import Callable, List, Optional, Sequence, Tuple

# ------------------------------ Utilities -------------------------------------


def _rng(seed: Optional[int]) -> random.Random:
    if seed is None:
        seed = int(os.environ.get("PYTHONHASHSEED", "0") or "1337")
    return random.Random(seed)


def _rand_bytes(r: random.Random, n: int) -> bytes:
    # Fast-enough pure-Python generator
    return bytes(r.getrandbits(8) for _ in range(n))


def _xor_many(shards: Sequence[bytes]) -> bytes:
    if not shards:
        return b""
    size = len(shards[0])
    acc = bytearray(size)
    for s in shards:
        # Assume all equal length
        bmv = memoryview(s)
        for i in range(size):
            acc[i] ^= bmv[i]
    return bytes(acc)


# --------------------------- Real RS adapters ---------------------------------


class RSAdapter:
    """
    Abstract adapter: wraps a concrete implementation to expose:
      - encode(data_shards: List[bytes]) -> List[bytes]  (length n)
      - decode(shards: List[Optional[bytes]]) -> List[bytes]  (length n)
    """

    def __init__(self, k: int, n: int, shard_size: int):
        self.k = k
        self.n = n
        self.shard_size = shard_size

    def encode(self, data_shards: List[bytes]) -> List[bytes]:
        raise NotImplementedError

    def decode(self, shards_with_gaps: List[Optional[bytes]]) -> List[bytes]:
        raise NotImplementedError


class RSAdapterFallback(RSAdapter):
    """
    Simple XOR parity simulator:
      - Produces (n-k) identical parity shards = XOR(data shards).
      - Can recover a single missing shard (either one data shard or any parity).
    """

    def encode(self, data_shards: List[bytes]) -> List[bytes]:
        assert len(data_shards) == self.k
        parity = _xor_many(data_shards)
        parities = [parity] * (self.n - self.k)
        return list(data_shards) + parities

    def decode(self, shards_with_gaps: List[Optional[bytes]]) -> List[bytes]:
        assert len(shards_with_gaps) == self.n
        # Count missing
        miss_idx = [i for i, s in enumerate(shards_with_gaps) if s is None]
        assert len(miss_idx) == 1, "fallback only supports single missing shard"
        j = miss_idx[0]

        # Rebuild data list for parity recompute
        data = [shards_with_gaps[i] for i in range(self.k)]
        parity_present = [s for s in shards_with_gaps[self.k :] if s is not None]
        # Parity to use (if missing parity, recompute from available data)
        parity = (
            parity_present[0]
            if parity_present
            else _xor_many([d for d in data if d is not None])
        )

        if j < self.k:
            # Missing a data shard → reconstruct using parity and remaining data
            present = [d for i, d in enumerate(data) if i != j and d is not None]
            rec = _xor_many(present + [parity])
            data[j] = rec
            rebuilt_parities = [_xor_many(data)] * (self.n - self.k)
            return [d for d in data] + rebuilt_parities
        else:
            # Missing a parity shard → recompute parity
            rebuilt_parity = _xor_many([d for d in data if d is not None])
            shards = list(shards_with_gaps)
            shards[j] = rebuilt_parity
            # Fill any other parity Nones (there shouldn't be any additional)
            for i in range(self.k, self.n):
                if shards[i] is None:
                    shards[i] = rebuilt_parity
            return [bytes(s) for s in shards]  # type: ignore[arg-type]


def _maybe_real_adapter(k: int, n: int, shard_size: int) -> Optional[RSAdapter]:
    """
    Try several shapes:
      - da.erasure.reedsolomon.encode(data, k, n) / decode(shards, k, n)
      - da.erasure.encoder.encode(data, k, n) / decode(shards, k, n)
      - da.erasure.reedsolomon.RS(k, n).encode(data) / .decode(shards)
    """
    try:
        import importlib

        for mod_name in ("da.erasure.reedsolomon", "da.erasure.encoder"):
            try:
                mod = importlib.import_module(mod_name)
            except Exception:
                continue

            # Case 1: free functions
            enc = getattr(mod, "encode", None)
            dec = getattr(mod, "decode", None)
            if callable(enc) and callable(dec):

                class _FF(RSAdapter):
                    def encode(self, data_shards: List[bytes]) -> List[bytes]:
                        try:
                            out = enc(data_shards, k=self.k, n=self.n)  # type: ignore[misc]
                        except TypeError:
                            out = enc(data_shards, self.k, self.n)  # type: ignore[misc]
                        return list(out)

                    def decode(
                        self, shards_with_gaps: List[Optional[bytes]]
                    ) -> List[bytes]:
                        # Replace None with b"" per some APIs, pass erasures list if supported
                        erasures = [
                            i for i, s in enumerate(shards_with_gaps) if s is None
                        ]
                        shards = [
                            s if s is not None else b"\x00" * self.shard_size
                            for s in shards_with_gaps
                        ]
                        try:
                            out = dec(shards, k=self.k, n=self.n, erasures=erasures)  # type: ignore[misc]
                        except TypeError:
                            try:
                                out = dec(shards, self.k, self.n, erasures)  # type: ignore[misc]
                            except Exception:
                                out = dec(shards, self.k, self.n)  # type: ignore[misc]
                        return list(out)

                return _FF(k, n, shard_size)

            # Case 2: class with encode/decode
            cls = getattr(mod, "RS", None)
            if cls is not None:
                try:
                    inst = cls(k, n)  # type: ignore
                    if hasattr(inst, "encode") and hasattr(inst, "decode"):

                        class _CLS(RSAdapter):
                            def __init__(self):
                                super().__init__(k, n, shard_size)
                                self._inst = inst

                            def encode(self, data_shards: List[bytes]) -> List[bytes]:
                                return list(self._inst.encode(data_shards))

                            def decode(
                                self, shards_with_gaps: List[Optional[bytes]]
                            ) -> List[bytes]:
                                return list(self._inst.decode(shards_with_gaps))

                        return _CLS()
                except Exception:
                    pass
    except Exception:
        return None
    return None


# ------------------------------ Benchmark Core --------------------------------


def _timeit(fn: Callable[[], None], repeats: int) -> Tuple[List[float], None]:
    timings: List[float] = []
    for _ in range(repeats):
        t0 = time.perf_counter()
        fn()
        t1 = time.perf_counter()
        timings.append(t1 - t0)
    return timings, None


def run_bench(
    k: int,
    n: int,
    shard: int,
    rounds: int,
    warmup: int,
    repeat: int,
    mode: str,
    seed: Optional[int],
) -> dict:
    assert n >= k >= 1
    r = _rng(seed)

    # Prepare single data batch and missing indices (we'll reuse to time codec only)
    data = [_rand_bytes(r, shard) for _ in range(k)]
    miss_idxs = [r.randrange(0, n) for _ in range(rounds)]

    # Choose adapter
    adapter: RSAdapter
    real = _maybe_real_adapter(k, n, shard) if mode == "auto" or mode == "da" else None
    if real and mode in ("auto", "da"):
        adapter = real
        label = "da"
    else:
        adapter = RSAdapterFallback(k, n, shard)
        label = "fallback"

    # Precompute encoded once for decode loop
    encoded_once = adapter.encode(data)

    # Encode timing
    def do_encode():
        # Re-encode the same data 'rounds' times
        enc_local = None
        for _ in range(rounds):
            enc_local = adapter.encode(data)
        # Prevent aggressive dead-code elimination by using the result
        if enc_local is None or len(enc_local) != n:
            raise AssertionError("encode produced no output")

    # Decode timing (single erasure per round)
    def do_decode():
        for j in miss_idxs:
            shards = list(encoded_once)
            shards[j] = None  # type: ignore
            out = adapter.decode(shards)  # type: ignore[arg-type]
            if len(out) != n:
                raise AssertionError("decode returned wrong shard count")

    # Warmup
    if warmup > 0:
        _timeit(do_encode, warmup)
        _timeit(do_decode, warmup)

    # Measure
    enc_timings, _ = _timeit(do_encode, repeat)
    dec_timings, _ = _timeit(do_decode, repeat)

    # Stats
    enc_median = statistics.median(enc_timings)
    enc_p90 = (
        statistics.quantiles(enc_timings, n=10)[8]
        if len(enc_timings) >= 10
        else max(enc_timings)
    )
    dec_median = statistics.median(dec_timings)
    dec_p90 = (
        statistics.quantiles(dec_timings, n=10)[8]
        if len(dec_timings) >= 10
        else max(dec_timings)
    )

    total_bytes = rounds * k * shard  # bytes processed per iteration (encode & decode)
    enc_MBps = (
        (total_bytes / enc_median) / 1_000_000 if enc_median > 0 else float("inf")
    )
    dec_MBps = (
        (total_bytes / dec_median) / 1_000_000 if dec_median > 0 else float("inf")
    )

    return {
        "case": f"da.rs_encode_decode(k={k},n={n},shard={shard})",
        "params": {
            "k": k,
            "n": n,
            "shard": shard,
            "rounds": rounds,
            "repeat": repeat,
            "warmup": warmup,
            "mode": label,
            "seed": (
                seed
                if seed is not None
                else int(os.environ.get("PYTHONHASHSEED", "0") or "1337")
            ),
        },
        "result": {
            "encode_MBps": enc_MBps,
            "decode_MBps": dec_MBps,
            "encode_median_s": enc_median,
            "encode_p90_s": enc_p90,
            "decode_median_s": dec_median,
            "decode_p90_s": dec_p90,
        },
    }


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(
        description="Reed–Solomon encode/decode throughput benchmark (MB/s)."
    )
    ap.add_argument(
        "--k", type=int, default=128, help="Number of data shards (default: 128)"
    )
    ap.add_argument(
        "--n", type=int, default=256, help="Total shards (data + parity) (default: 256)"
    )
    ap.add_argument(
        "--shard", type=int, default=4096, help="Shard size in bytes (default: 4096)"
    )
    ap.add_argument(
        "--rounds",
        type=int,
        default=200,
        help="Encode/decode rounds per iteration (default: 200)",
    )
    ap.add_argument(
        "--warmup", type=int, default=1, help="Warmup iterations (default: 1)"
    )
    ap.add_argument(
        "--repeat", type=int, default=5, help="Measured iterations (default: 5)"
    )
    ap.add_argument(
        "--mode",
        choices=("auto", "da", "fallback"),
        default="auto",
        help="Use real da.erasure codec if available, else fallback (default: auto)",
    )
    ap.add_argument(
        "--seed",
        type=int,
        default=None,
        help="PRNG seed (default: from PYTHONHASHSEED or 1337)",
    )
    args = ap.parse_args(argv)

    payload = run_bench(
        k=args.k,
        n=args.n,
        shard=args.shard,
        rounds=args.rounds,
        warmup=args.warmup,
        repeat=args.repeat,
        mode=args.mode,
        seed=args.seed,
    )

    print(json.dumps(payload, separators=(",", ":"), sort_keys=False))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
