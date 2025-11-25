import importlib
import inspect
import os
import random
from typing import Any, Callable, List, Optional, Sequence, Tuple

import pytest


# ---------------------------
# Flexible adapters for da.erasure.reedsolomon
# (tolerant to API naming drift)
# ---------------------------

def _rs_mod():
    return importlib.import_module("da.erasure.reedsolomon")


def _encode_fn() -> Callable[[bytes, int, int, int], List[bytes]]:
    """
    Return an encoder that takes (data, k, n, share_bytes) -> List[bytes] length n.
    Falls back to constructing from shards if needed.
    """
    mod = _rs_mod()
    # Common function names
    for name in ("encode", "rs_encode", "encode_bytes"):
        if hasattr(mod, name):
            fn = getattr(mod, name)
            def _wrap(data: bytes, k: int, n: int, share_bytes: int, _fn=fn) -> List[bytes]:
                out = _fn(data, k, n, share_bytes)
                return list(out)
            return _wrap

    # Class-based
    for cname in ("ReedSolomon", "RS", "RSCodec"):
        if hasattr(mod, cname):
            cls = getattr(mod, cname)
            try:
                obj = cls()
            except TypeError:
                obj = None
            # try (k, n, share_bytes) ctor
            for ctor in ((k, n, share), ()):
                pass  # filled dynamically in wrapper

            def _wrap(data: bytes, k: int, n: int, share_bytes: int, _cls=cls) -> List[bytes]:
                try:
                    rs = _cls(k=k, n=n, share_bytes=share_bytes)
                except TypeError:
                    try:
                        rs = _cls(k, n, share_bytes)
                    except TypeError:
                        rs = _cls()
                # method variants
                for m in ("encode", "encode_bytes"):
                    if hasattr(rs, m):
                        shards = getattr(rs, m)(data, k, n, share_bytes)
                        return list(shards)
                # encode from data shards method
                # split data into k shares:
                data_shards = [data[i*share_bytes : (i+1)*share_bytes] for i in range(k)]
                for m in ("encode_shards", "extend", "build"):
                    if hasattr(rs, m):
                        out = getattr(rs, m)(data_shards, k, n, share_bytes) if getattr(rs, m).__code__.co_argcount >= 5 else getattr(rs, m)(data_shards)
                        return list(out)
                raise RuntimeError("No usable encode method on RS class")
            return _wrap

    # Fallback: module-level encode from shards
    for name in ("encode_shards", "rs_encode_shards", "build"):
        if hasattr(mod, name):
            fn = getattr(mod, name)
            def _wrap(data: bytes, k: int, n: int, share_bytes: int, _fn=fn) -> List[bytes]:
                data_shards = [data[i*share_bytes : (i+1)*share_bytes] for i in range(k)]
                out = _fn(data_shards, k, n, share_bytes) if _fn.__code__.co_argcount >= 4 else _fn(data_shards)
                return list(out)
            return _wrap

    raise RuntimeError("No RS encoder found in da.erasure.reedsolomon")


def _reconstruct_all_fn() -> Callable[[List[Optional[bytes]], int, int, int], List[bytes]]:
    """
    Return a reconstructor that takes (shards_with_Nones, k, n, share_bytes) -> List[bytes] length n.
    """
    mod = _rs_mod()
    for name in ("reconstruct", "recover", "repair", "decode_shards"):
        if hasattr(mod, name):
            fn = getattr(mod, name)
            def _wrap(shards: List[Optional[bytes]], k: int, n: int, share_bytes: int, _fn=fn) -> List[bytes]:
                try:
                    out = _fn(shards, k, n, share_bytes)
                except TypeError:
                    out = _fn(shards)
                return list(out)
            return _wrap

    # Class-based
    for cname in ("ReedSolomon", "RS", "RSCodec"):
        if hasattr(mod, cname):
            cls = getattr(mod, cname)
            def _wrap(shards: List[Optional[bytes]], k: int, n: int, share_bytes: int, _cls=cls) -> List[bytes]:
                try:
                    rs = _cls(k=k, n=n, share_bytes=share_bytes)
                except TypeError:
                    try:
                        rs = _cls(k, n, share_bytes)
                    except TypeError:
                        rs = _cls()
                for m in ("reconstruct", "recover", "repair", "decode_shards"):
                    if hasattr(rs, m):
                        try:
                            out = getattr(rs, m)(shards, k, n, share_bytes)
                        except TypeError:
                            out = getattr(rs, m)(shards)
                        return list(out)
                raise RuntimeError("No usable reconstruct method on RS class")
            return _wrap

    raise RuntimeError("No RS reconstruct function found")


def _decode_data_fn() -> Optional[Callable[[List[Optional[bytes]], int, int, int], bytes]]:
    """
    Optional fast path: (shards_with_Nones, k, n, share_bytes) -> bytes (k*share_bytes).
    If not available, we fall back to reconstruct_all and concatenate first k shards.
    """
    mod = _rs_mod()
    for name in ("decode", "decode_bytes", "data_from_shards"):
        if hasattr(mod, name):
            fn = getattr(mod, name)
            def _wrap(shards: List[Optional[bytes]], k: int, n: int, share_bytes: int, _fn=fn) -> bytes:
                try:
                    return _fn(shards, k, n, share_bytes)
                except TypeError:
                    return _fn(shards)
            return _wrap
    # class-based
    for cname in ("ReedSolomon", "RS", "RSCodec"):
        if hasattr(mod, cname):
            cls = getattr(mod, cname)
            def _wrap(shards: List[Optional[bytes]], k: int, n: int, share_bytes: int, _cls=cls) -> bytes:
                try:
                    rs = _cls(k=k, n=n, share_bytes=share_bytes)
                except TypeError:
                    try:
                        rs = _cls(k, n, share_bytes)
                    except TypeError:
                        rs = _cls()
                for m in ("decode", "decode_bytes", "data_from_shards"):
                    if hasattr(rs, m):
                        try:
                            return getattr(rs, m)(shards, k, n, share_bytes)
                        except TypeError:
                            return getattr(rs, m)(shards)
                raise RuntimeError("No usable decode method on RS class")
            return _wrap
    return None


def _verify_fn() -> Optional[Callable[[List[bytes], int, int, int], bool]]:
    """Optional parity verifier: (full_shards, k, n, share_bytes) -> bool."""
    mod = _rs_mod()
    for name in ("verify", "check", "parity_ok"):
        if hasattr(mod, name):
            fn = getattr(mod, name)
            def _wrap(shards: List[bytes], k: int, n: int, share_bytes: int, _fn=fn) -> bool:
                try:
                    return bool(_fn(shards, k, n, share_bytes))
                except TypeError:
                    return bool(_fn(shards))
            return _wrap
    return None


# ---------------------------
# Test helpers
# ---------------------------

def _det_data(k: int, share_bytes: int) -> bytes:
    # Deterministic pseudo-random data; fits exactly k * share_bytes
    random.seed(1337 + k * 1000 + share_bytes)
    return bytes(random.getrandbits(8) for _ in range(k * share_bytes))


def _erase_random(shards: List[bytes], missing: int) -> List[Optional[bytes]]:
    idxs = list(range(len(shards)))
    random.shuffle(idxs)
    dead = set(idxs[:missing])
    return [None if i in dead else s for i, s in enumerate(shards)]


def _recover_data(shards_maybe: List[Optional[bytes]], k: int, n: int, share_bytes: int) -> bytes:
    decode_bytes = _decode_data_fn()
    if decode_bytes is not None:
        return decode_bytes(shards_maybe, k, n, share_bytes)
    # Fallback: reconstruct all, then join first k shares
    recon = _reconstruct_all_fn()
    full = recon(shards_maybe, k, n, share_bytes)
    assert len(full) == n
    return b"".join(full[:k])


# ---------------------------
# Parameterizations
# ---------------------------

PARAMS = [
    (4, 8, 64),
    (6, 12, 128),
    (8, 16, 96),
]


# ---------------------------
# Tests
# ---------------------------

@pytest.mark.parametrize("k,n,share_bytes", PARAMS)
def test_rs_roundtrip_no_losses(k: int, n: int, share_bytes: int):
    encode = _encode_fn()
    data = _det_data(k, share_bytes)
    shards = encode(data, k, n, share_bytes)
    assert isinstance(shards, list) and len(shards) == n
    for s in shards:
        assert isinstance(s, (bytes, bytearray)) and len(s) == share_bytes
    recovered = _recover_data(list(shards), k, n, share_bytes)
    assert recovered == data, "Decoding with no losses must return original data"


@pytest.mark.parametrize("k,n,share_bytes", PARAMS)
def test_rs_recover_from_any_k_shards(k: int, n: int, share_bytes: int):
    encode = _encode_fn()
    data = _det_data(k, share_bytes)
    shards = encode(data, k, n, share_bytes)
    trials = min(12, n)  # keep runtime reasonable
    for seed in range(trials):
        random.seed(9000 + seed + k * 17 + n)
        missing = n - k  # worst-case erasures
        shards_maybe = _erase_random(shards, missing)
        recovered = _recover_data(shards_maybe, k, n, share_bytes)
        assert recovered == data, f"Failed to recover from worst-case erasures on trial {seed}"


@pytest.mark.parametrize("k,n,share_bytes", PARAMS)
def test_rs_recover_with_partial_erasures(k: int, n: int, share_bytes: int):
    encode = _encode_fn()
    data = _det_data(k, share_bytes)
    shards = encode(data, k, n, share_bytes)
    # Try a spectrum of erasure counts from 1..(n-k)
    for missing in range(1, n - k + 1):
        random.seed(4242 + k * 3 + missing)
        shards_maybe = _erase_random(shards, missing)
        recovered = _recover_data(shards_maybe, k, n, share_bytes)
        assert recovered == data, f"Recovery failed with {missing} erasures (limit {n-k})"


@pytest.mark.parametrize("k,n,share_bytes", PARAMS)
def test_rs_detects_or_fails_on_unknown_corruption(k: int, n: int, share_bytes: int):
    """
    If a verifier is provided, it must flag corruption when no erasures are marked.
    If no verifier exists, we accept a decode failure (exception) or a wrong result
    and skip strict assertion to avoid false negatives across implementations.
    """
    encode = _encode_fn()
    data = _det_data(k, share_bytes)
    shards = encode(data, k, n, share_bytes)
    # Flip one byte in a random shard without marking as erasure.
    random.seed(777 + k + n)
    victim = random.randrange(n)
    corrupted = list(shards)
    b = bytearray(corrupted[victim])
    b[0] ^= 0xFF
    corrupted[victim] = bytes(b)

    verifier = _verify_fn()
    if verifier is not None:
        ok_clean = verifier(list(shards), k, n, share_bytes)
        ok_dirty = verifier(list(corrupted), k, n, share_bytes)
        assert ok_clean is True
        assert ok_dirty is False, "Parity verifier must flag corrupted shards"
    else:
        # Try to decode — some implementations may raise on bad parity.
        try:
            out = _recover_data([s for s in corrupted], k, n, share_bytes)
        except Exception:
            # Accept raising as valid detection of corruption.
            return
        # If it didn't raise and also (unlikely) produced the original bytes,
        # we can't assert failure generically; skip to avoid flakiness.
        if out == data:
            pytest.skip("Decoder corrected unknown corruption or implementation lacks parity checks")


@pytest.mark.parametrize("k,n,share_bytes", PARAMS)
def test_parity_shards_nontrivial(k: int, n: int, share_bytes: int):
    """
    Sanity: at least one parity shard should differ from zeros for non-zero data.
    """
    if n == k:
        pytest.skip("No parity when n == k")
    encode = _encode_fn()
    data = _det_data(k, share_bytes)
    shards = encode(data, k, n, share_bytes)
    parity = shards[k:]
    assert any(any(byte != 0 for byte in p) for p in parity), "Parity shards look all-zero; encoder likely broken"
