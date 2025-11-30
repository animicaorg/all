#!/usr/bin/env python3
import hashlib
import os
import random
import struct
import time

TARGET_BITS = 0x1F00FFFF


def compact_to_target(bits: int) -> int:
    exp = bits >> 24
    mant = bits & 0xFFFFFF
    return mant << (8 * (exp - 3))


target = compact_to_target(TARGET_BITS)
header_prefix = bytes.fromhex("01000000" + ("00" * 32) + ("11" * 32) + "ffffffff")


def sha256d(b: bytes) -> bytes:
    return hashlib.sha256(hashlib.sha256(b).digest()).digest()


def pure(limit=1_000_000):
    start = time.time()
    found = 0
    base = random.randrange(0, 2**32 - 1)
    for i in range(limit):
        nonce = (base + i) & 0xFFFFFFFF
        h = sha256d(header_prefix + struct.pack("<I", nonce))
        hv = int.from_bytes(h[::-1], "big")
        if hv <= target:
            found += 1
            print(f"[pure] share nonce={nonce} hash={h.hex()}")
    dt = time.time() - start
    print(f"[pure] tested {limit:,} in {dt:.2f}s  ~{limit/dt:,.0f} H/s shares={found}")


try:
    import importlib

    mh = importlib.import_module("mining.hash_search")
    func = None
    for name in ("search", "scan_range", "cpu_search", "search_cpu"):
        if hasattr(mh, name):
            func = getattr(mh, name)
            break
    if func:
        print(f"[repo] using mining.hash_search.{func.__name__}")
        ret = func(header_prefix, 0, 1_000_000, target)  # best-effort signature
        out = (
            list(ret) if ret is not None and not isinstance(ret, list) else (ret or [])
        )
        print(f"[repo] shares found: {len(out)}")
    else:
        pure()
except Exception as e:
    print(f"[repo] failed: {e}; falling back to pure…")
    pure()
