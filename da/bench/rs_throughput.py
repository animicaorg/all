#!/usr/bin/env python3
"""
Reed–Solomon (RS) encode/decode throughput benchmark for Animica DA.

This script tries to discover a usable RS(k, n) reference implementation from
`da.erasure.reedsolomon` (preferred) or a higher-level helper from
`da.erasure.encoder`. It is defensive about API shapes — it will probe a few
common method/function names and signatures.

What it measures
----------------
• Encode: data shards → n total shards (data + parity)
• Decode: recover from `--losses` erased shards and verify the original data

Throughput is reported in MiB/s and operations/sec. Roots/bytes are verified
across rounds and the decode step is checked for correctness.

Examples
--------
  python -m da.bench.rs_throughput
  python -m da.bench.rs_throughput --k 64 --n 96 --shard-size 2048 --rounds 5
  python -m da.bench.rs_throughput --losses 10 --seed 1337

Notes
-----
• Losses must be <= parity (n - k); the tool will clamp if necessary.
• If your RS implementation prefers a different erased-shard marker than None,
  the bench will try a couple of fallbacks automatically.
"""

from __future__ import annotations

import argparse
import random
import statistics
import time
from typing import Any, Callable, Iterable, List, Optional, Sequence, Tuple

# -----------------------------
# Generic import helpers
# -----------------------------

def _import(mod: str):
    return __import__(mod, fromlist=["*"])


# -----------------------------
# RS implementation discovery
# -----------------------------

class _RSWrapper:
    """
    Normalized interface around whichever RS implementation we discover.
    Must provide:
      - encode_shards(data_shards: List[bytes]) -> List[bytes] (len == n)
      - decode_shards(shards: List[Optional[bytes]], erasures: List[int]) -> List[bytes] (len == n)
    """
    def __init__(self, k: int, n: int):
        self.k = k
        self.n = n
        self._impl = None
        self._encode_adapter: Optional[Callable[[List[bytes]], List[bytes]]] = None
        self._decode_adapter: Optional[Callable[[List[Optional[bytes]], List[int]], List[bytes]]] = None
        self._discover()

    # --- public API ---

    def encode_shards(self, data_shards: List[bytes]) -> List[bytes]:
        assert len(data_shards) == self.k, "encode_shards expects k data shards"
        if not self._encode_adapter:
            raise RuntimeError("encode adapter not available")
        return self._encode_adapter(data_shards)

    def decode_shards(self, shards: List[Optional[bytes]], erasures: List[int]) -> List[bytes]:
        assert len(shards) == self.n, "decode_shards expects n shards (None for erased)"
        if not self._decode_adapter:
            raise RuntimeError("decode adapter not available")
        return self._decode_adapter(shards, erasures)

    # --- discovery ---

    def _discover(self) -> None:
        # Preferred: class in da.erasure.reedsolomon
        try:
            rs_mod = _import("da.erasure.reedsolomon")
        except ModuleNotFoundError:
            rs_mod = None

        if rs_mod:
            # Class names we try
            for cls_name in ("ReedSolomon", "RS", "Codec", "ReedSolomonCodec"):
                if hasattr(rs_mod, cls_name):
                    C = getattr(rs_mod, cls_name)
                    try:
                        inst = C(self.k, self.n)
                    except TypeError:
                        # Some variants take (k, parity) or keyword args
                        try:
                            inst = C(k=self.k, n=self.n)
                        except Exception:
                            continue
                    # Build adapters off instance methods
                    enc = self._mk_inst_encoder(inst)
                    dec = self._mk_inst_decoder(inst)
                    if enc and dec:
                        self._impl = inst
                        self._encode_adapter = enc
                        self._decode_adapter = dec
                        return

            # Module-level functions
            enc = self._mk_module_encoder(rs_mod)
            dec = self._mk_module_decoder(rs_mod)
            if enc and dec:
                self._impl = rs_mod
                self._encode_adapter = enc
                self._decode_adapter = dec
                return

        # Fallback: try higher-level encoder that might expose RS helpers
        try:
            enc_mod = _import("da.erasure.encoder")
        except ModuleNotFoundError:
            enc_mod = None

        if enc_mod:
            # Common helper names
            # e.g., build_rs(k, n) -> object with encode/decode, or encode_rs_bytes(...)
            for name in ("build_rs", "make_rs", "codec", "new_rs"):
                if hasattr(enc_mod, name):
                    try:
                        inst = getattr(enc_mod, name)(self.k, self.n)
                        enc = self._mk_inst_encoder(inst)
                        dec = self._mk_inst_decoder(inst)
                        if enc and dec:
                            self._impl = inst
                            self._encode_adapter = enc
                            self._decode_adapter = dec
                            return
                    except Exception:
                        pass

            # Module-level fallbacks
            enc = self._mk_module_encoder(enc_mod)
            dec = self._mk_module_decoder(enc_mod)
            if enc and dec:
                self._impl = enc_mod
                self._encode_adapter = enc
                self._decode_adapter = dec
                return

        raise SystemExit("Could not find a usable RS implementation in da.erasure.reedsolomon or da.erasure.encoder")

    # --- adapter builders ---

    def _mk_inst_encoder(self, inst: Any) -> Optional[Callable[[List[bytes]], List[bytes]]]:
        for meth in ("encode", "encode_shards", "encode_blocks", "build"):
            if hasattr(inst, meth):
                fn = getattr(inst, meth)
                def adapter(data_shards: List[bytes], _fn=fn) -> List[bytes]:
                    # Try several calling conventions
                    # 1) fn(data_shards)
                    try:
                        out = _fn(data_shards)
                        return self._normalize_encode_output(out, data_shards)
                    except Exception:
                        pass
                    # 2) fn(k, n, data_shards)
                    try:
                        out = _fn(self.k, self.n, data_shards)
                        return self._normalize_encode_output(out, data_shards)
                    except Exception:
                        pass
                    # 3) fn(data_shards, parity=n-k)
                    try:
                        out = _fn(data_shards, self.n - self.k)
                        return self._normalize_encode_output(out, data_shards)
                    except Exception:
                        pass
                    # 4) fn(data=data_shards)
                    try:
                        out = _fn(data=data_shards)
                        return self._normalize_encode_output(out, data_shards)
                    except Exception:
                        pass
                    raise RuntimeError("instance encode failed for all patterns")
                return adapter
        return None

    def _mk_inst_decoder(self, inst: Any) -> Optional[Callable[[List[Optional[bytes]], List[int]], List[bytes]]]:
        for meth in ("decode", "decode_shards", "reconstruct", "recover"):
            if hasattr(inst, meth):
                fn = getattr(inst, meth)
                def adapter(shards: List[Optional[bytes]], erasures: List[int], _fn=fn) -> List[bytes]:
                    # Provide a couple of placeholder styles for erased shards
                    candidates = [
                        shards,
                        self._with_zero_fill(shards),
                    ]
                    # Try calling conventions
                    for s in candidates:
                        # 1) fn(shards, erasures)
                        try:
                            out = _fn(s, erasures)
                            return self._normalize_decode_output(out, s)
                        except Exception:
                            pass
                        # 2) fn(k, n, shards, erasures)
                        try:
                            out = _fn(self.k, self.n, s, erasures)
                            return self._normalize_decode_output(out, s)
                        except Exception:
                            pass
                        # 3) fn(shards, erasures, k=self.k, n=self.n)
                        try:
                            out = _fn(s, erasures, k=self.k, n=self.n)
                            return self._normalize_decode_output(out, s)
                        except Exception:
                            pass
                    raise RuntimeError("instance decode failed for all patterns/placeholders")
                return adapter
        return None

    def _mk_module_encoder(self, mod: Any) -> Optional[Callable[[List[bytes]], List[bytes]]]:
        for name in ("encode", "encode_shards", "rs_encode", "encode_rs"):
            if hasattr(mod, name):
                fn = getattr(mod, name)
                def adapter(data_shards: List[bytes], _fn=fn) -> List[bytes]:
                    # 1) fn(data_shards, k, n)
                    try:
                        out = _fn(data_shards, self.k, self.n)
                        return self._normalize_encode_output(out, data_shards)
                    except Exception:
                        pass
                    # 2) fn(k, n, data_shards)
                    try:
                        out = _fn(self.k, self.n, data_shards)
                        return self._normalize_encode_output(out, data_shards)
                    except Exception:
                        pass
                    # 3) fn(data_shards, parity=n-k)
                    try:
                        out = _fn(data_shards, self.n - self.k)
                        return self._normalize_encode_output(out, data_shards)
                    except Exception:
                        pass
                    raise RuntimeError("module encode failed for all patterns")
                return adapter
        return None

    def _mk_module_decoder(self, mod: Any) -> Optional[Callable[[List[Optional[bytes]], List[int]], List[bytes]]]:
        for name in ("decode", "decode_shards", "rs_decode", "reconstruct"):
            if hasattr(mod, name):
                fn = getattr(mod, name)
                def adapter(shards: List[Optional[bytes]], erasures: List[int], _fn=fn) -> List[bytes]:
                    candidates = [shards, self._with_zero_fill(shards)]
                    for s in candidates:
                        # 1) fn(shards, erasures, k, n)
                        try:
                            out = _fn(s, erasures, self.k, self.n)
                            return self._normalize_decode_output(out, s)
                        except Exception:
                            pass
                        # 2) fn(k, n, shards, erasures)
                        try:
                            out = _fn(self.k, self.n, s, erasures)
                            return self._normalize_decode_output(out, s)
                        except Exception:
                            pass
                        # 3) fn(shards, erasures)
                        try:
                            out = _fn(s, erasures)
                            return self._normalize_decode_output(out, s)
                        except Exception:
                            pass
                    raise RuntimeError("module decode failed for all patterns/placeholders")
                return adapter
        return None

    # --- normalization ---

    def _normalize_encode_output(self, out: Any, data_shards: List[bytes]) -> List[bytes]:
        if isinstance(out, (bytes, bytearray)):
            # Unexpected: treat as concatenated shards; split equally
            size = len(data_shards[0])
            return [bytes(out[i*size:(i+1)*size]) for i in range(self.n)]
        if isinstance(out, tuple) and len(out) == 2:
            # (data_shards, parity_shards)
            d, p = out
            shards = list(d) + list(p)
            if len(shards) != self.n:
                raise RuntimeError("encode returned tuple but total shard count != n")
            return [bytes(b) for b in shards]
        if isinstance(out, list) and len(out) == self.n:
            return [bytes(b) if b is not None else b"" for b in out]
        # Some encoders return only parity shards
        if isinstance(out, list) and len(out) == (self.n - self.k):
            return list(data_shards) + [bytes(b) for b in out]
        raise RuntimeError("encode output could not be normalized to n shards")

    def _normalize_decode_output(self, out: Any, shards_in: List[Optional[bytes]]) -> List[bytes]:
        if isinstance(out, list) and len(out) == self.n:
            return [bytes(b) if b is not None else b"" for b in out]
        # Some decoders return only reconstructed data shards
        if isinstance(out, list) and len(out) == self.k:
            # Merge back with (possibly) provided parity shards from input
            data = [bytes(b) for b in out]
            parity = []
            size = len(next(b for b in shards_in if b is not None))
            # If parity shards were present in input, keep them; else fill from data copy (not ideal, but bench is about correctness of data shards)
            for i in range(self.k, self.n):
                parity.append(shards_in[i] if shards_in[i] is not None else b"\x00" * size)
            return data + [bytes(b) for b in parity]
        raise RuntimeError("decode output could not be normalized to n shards")

    @staticmethod
    def _with_zero_fill(shards: List[Optional[bytes]]) -> List[bytes]:
        size = None
        for b in shards:
            if b is not None:
                size = len(b)
                break
        if size is None:
            size = 0
        return [b if b is not None else (b"\x00" * size) for b in shards]


# -----------------------------
# Data generation & bench core
# -----------------------------

def _make_data_shards(k: int, shard_size: int, seed: int) -> List[bytes]:
    rnd = random.Random(seed)
    shards: List[bytes] = []
    for _ in range(k):
        if hasattr(rnd, "randbytes"):
            shards.append(rnd.randbytes(shard_size))  # py3.9+
        else:
            shards.append(bytes(rnd.getrandbits(8) for _ in range(shard_size)))
    return shards


def _erase_random(shards: List[bytes], losses: int, seed: int) -> Tuple[List[Optional[bytes]], List[int]]:
    rnd = random.Random(seed ^ 0xE1A5)
    n = len(shards)
    erasures = sorted(rnd.sample(range(n), k=losses))
    erased: List[Optional[bytes]] = []
    for i, b in enumerate(shards):
        erased.append(None if i in erasures else b)
    return erased, erasures


def _sizeof(num_bytes: int) -> str:
    return f"{num_bytes/1024/1024:.2f} MiB"


# -----------------------------
# Benchmark runners
# -----------------------------

def bench_encode(rs: _RSWrapper, data_shards: List[bytes], rounds: int, warmup: int) -> Tuple[List[float], List[List[bytes]]]:
    times: List[float] = []
    outputs: List[List[bytes]] = []
    # Warmup
    for _ in range(warmup):
        _ = rs.encode_shards(data_shards)
    # Timed
    for _ in range(rounds):
        t0 = time.perf_counter()
        shards = rs.encode_shards(data_shards)
        dt = time.perf_counter() - t0
        times.append(dt)
        outputs.append(shards)
    return times, outputs


def bench_decode(rs: _RSWrapper, encoded_shards: List[bytes], losses: int, rounds: int, warmup: int, seed: int) -> Tuple[List[float], None]:
    times: List[float] = []
    # Warmup
    e0, idx0 = _erase_random(encoded_shards, losses, seed)
    for _ in range(warmup):
        _ = rs.decode_shards(e0, idx0)
    # Timed (vary erasures per round a bit)
    for i in range(rounds):
        erased, erasures = _erase_random(encoded_shards, losses, seed + i + 1)
        t0 = time.perf_counter()
        recovered = rs.decode_shards(erased, erasures)
        dt = time.perf_counter() - t0
        times.append(dt)
        # verify data shards equal original data shards after recovery
        assert recovered[:rs.k] == encoded_shards[:rs.k], "Recovered data shards differ from original"
    return times, None


# -----------------------------
# Main
# -----------------------------

def main():
    ap = argparse.ArgumentParser(description="RS encode/decode throughput benchmark")
    ap.add_argument("--k", type=int, default=64, help="Number of data shards (default: 64)")
    ap.add_argument("--n", type=int, default=96, help="Total shards (data+parity), default: 96")
    ap.add_argument("--shard-size", type=int, default=1024, help="Bytes per shard (default: 1024)")
    ap.add_argument("--rounds", type=int, default=5, help="Timed rounds (default: 5)")
    ap.add_argument("--warmup", type=int, default=1, help="Warmup iterations (default: 1)")
    ap.add_argument("--losses", type=int, default=-1, help="Erasures in decode; default=min(parity, 8)")
    ap.add_argument("--seed", type=int, default=0xDA5EED, help="PRNG seed (default: 0xDA5EED)")
    args = ap.parse_args()

    if args.n <= args.k:
        raise SystemExit("n must be > k")
    parity = args.n - args.k
    losses = args.losses if args.losses >= 0 else min(parity, 8)
    if losses > parity:
        print(f"[bench] losses ({losses}) > parity ({parity}); clamping to {parity}")
        losses = parity

    total_data = args.k * args.shard_size
    print(f"[bench] RS(k={args.k}, n={args.n}), shard_size={args.shard_size} bytes "
          f"⇒ data={_sizeof(total_data)}, parity={parity}, losses={losses}")

    # Prepare data & codec
    data_shards = _make_data_shards(args.k, args.shard_size, args.seed)
    rs = _RSWrapper(args.k, args.n)

    # Encode
    enc_times, enc_outputs = bench_encode(rs, data_shards, args.rounds, args.warmup)
    # Verify all encode roots match
    ref = enc_outputs[0]
    for out in enc_outputs[1:]:
        if out != ref:
            raise SystemExit("[bench] ERROR: encode outputs differ across rounds")

    # Decode (with erasures)
    dec_times, _ = bench_decode(rs, ref, losses, args.rounds, args.warmup, args.seed)

    # Summaries
    def _summ(times: List[float]) -> Tuple[float, float, float]:
        mean = statistics.mean(times)
        p50 = statistics.median(times)
        p95 = statistics.quantiles(times, n=100)[94] if len(times) >= 2 else mean
        return mean, p50, p95

    enc_mean, enc_p50, enc_p95 = _summ(enc_times)
    dec_mean, dec_p50, dec_p95 = _summ(dec_times)

    enc_mib_s = (total_data / (1024 * 1024)) / enc_mean if enc_mean > 0 else float("inf")
    dec_mib_s = (total_data / (1024 * 1024)) / dec_mean if dec_mean > 0 else float("inf")

    print("\n[encode]")
    for i, t in enumerate(enc_times, 1):
        mbps = (total_data / (1024 * 1024)) / t if t > 0 else float("inf")
        print(f"  round {i}/{len(enc_times)}: {t*1000:.1f} ms  |  {mbps:8.2f} MiB/s")

    print("\n[decode]")
    for i, t in enumerate(dec_times, 1):
        mbps = (total_data / (1024 * 1024)) / t if t > 0 else float("inf")
        print(f"  round {i}/{len(dec_times)}: {t*1000:.1f} ms  |  {mbps:8.2f} MiB/s")

    print("\n[summary]")
    print(f"  encode: mean={enc_mean*1000:.1f} ms  p50={enc_p50*1000:.1f} ms  p95={enc_p95*1000:.1f} ms  "
          f"throughput={enc_mib_s:.2f} MiB/s")
    print(f"  decode: mean={dec_mean*1000:.1f} ms  p50={dec_p50*1000:.1f} ms  p95={dec_p95*1000:.1f} ms  "
          f"throughput={dec_mib_s:.2f} MiB/s")

if __name__ == "__main__":
    main()
