from __future__ import annotations

import os
from typing import Callable, Optional, Tuple

import pytest

# We try to be resilient to small API differences in vm_py.runtime.random_api.
# The helpers below discover either a stateful RNG (preferred) or a stateless
# function that returns deterministic bytes for (seed, length).


def _import_random_api():
    try:
        import vm_py.runtime.random_api as rnd  # type: ignore
    except Exception as e:  # pragma: no cover
        raise AssertionError(f"Failed to import vm_py.runtime.random_api: {e}")
    return rnd


def _find_stateful_rng(rnd_mod) -> Optional[Callable[[bytes], Callable[[int], bytes]]]:
    """
    If available, returns a constructor make(seed)->read(n)->bytes for a STATEFUL RNG.
    We probe common class/function names and read methods.
    """
    # Class-based RNGs
    for cls_name in ("DeterministicRNG", "PRNG", "RNG", "Random"):
        cls = getattr(rnd_mod, cls_name, None)
        if cls is None:
            continue
        try:
            # Try simple constructor: cls(seed: bytes)
            def _ctor(seed: bytes, _C=cls):
                inst = _C(seed)  # type: ignore[call-arg]
                for meth in ("read", "bytes", "next_bytes", "random_bytes", "get"):
                    fn = getattr(inst, meth, None)
                    if callable(fn):
                        return lambda n, _fn=fn: bytes(_fn(n))  # normalize to bytes
                # Some RNGs might be callable: inst(n)->bytes
                if callable(inst):
                    return lambda n, _inst=inst: bytes(_inst(n))
                raise TypeError("No read/bytes method on RNG instance")

            # Smoke-test the constructor without fixing the seed in case of validation
            _ = _ctor(os.urandom(32))  # ensure it doesn't raise
            return _ctor
        except Exception:
            # Try alt constructor shapes below
            pass

    # Factory functions that return an instance
    for fname in ("new", "make_rng", "rng", "init", "create"):
        fn = getattr(rnd_mod, fname, None)
        if not callable(fn):
            continue
        try:

            def _ctor(seed: bytes, _fn=fn):
                inst = _fn(seed)  # type: ignore[call-arg]
                for meth in ("read", "bytes", "next_bytes", "random_bytes", "get"):
                    f2 = getattr(inst, meth, None)
                    if callable(f2):
                        return lambda n, _f2=f2: bytes(_f2(n))
                if callable(inst):
                    return lambda n, _inst=inst: bytes(_inst(n))
                raise TypeError("No read/bytes method on RNG instance")

            _ = _ctor(os.urandom(32))
            return _ctor
        except Exception:
            continue

    return None


def _find_stateless_fn(rnd_mod) -> Optional[Callable[[bytes, int], bytes]]:
    """
    Returns a function bytes_for(seed, n) -> bytes for STATELESS APIs.
    """
    for fname in ("random_bytes", "bytes_for_seed", "deterministic_bytes", "bytes"):
        fn = getattr(rnd_mod, fname, None)
        if callable(fn):

            def _wrap(seed: bytes, n: int, _fn=fn):
                # Try common signatures
                last_err = None
                for call in (
                    lambda: _fn(seed, n),
                    lambda: _fn(seed=seed, length=n),
                    lambda: _fn(seed=seed, n=n),
                    lambda: _fn(n=n, seed=seed),
                ):
                    try:
                        out = call()
                        return bytes(out)
                    except Exception as e:
                        last_err = e
                        continue
                raise AssertionError(
                    f"Stateless RNG call failed; last error: {last_err}"
                )

            # Quick smoke test:
            _ = _wrap(os.urandom(32), 1)
            return _wrap
    return None


RND = _import_random_api()
STATEFUL_CTOR = _find_stateful_rng(RND)
STATELESS_BYTES = _find_stateless_fn(RND)


def _require_any_rng():
    if not STATEFUL_CTOR and not STATELESS_BYTES:
        pytest.skip("No compatible RNG API found in vm_py.runtime.random_api")


def _one_shot(seed: bytes, n: int) -> bytes:
    """
    Produce n bytes deterministically for (seed, n) using whichever API exists.
    """
    if STATELESS_BYTES:
        return STATELESS_BYTES(seed, n)
    if STATEFUL_CTOR:
        read = STATEFUL_CTOR(seed)
        return read(n)
    raise AssertionError("No RNG available")


@pytest.mark.parametrize("n", [0, 1, 16, 32, 64, 127, 256])
def test_same_seed_same_output(n: int):
    _require_any_rng()
    seed = b"\x11" * 32
    a = _one_shot(seed, n)
    b = _one_shot(seed, n)
    assert a == b
    assert isinstance(a, (bytes, bytearray))
    assert len(a) == n


@pytest.mark.parametrize("n", [32, 64, 128])
def test_different_seeds_different_output(n: int):
    _require_any_rng()
    seed1 = b"\xaa" * 32
    seed2 = b"\xab" * 32
    out1 = _one_shot(seed1, n)
    out2 = _one_shot(seed2, n)
    # Deterministic PRNG should differ for different seeds; collision here would be astronomically unlikely.
    assert out1 != out2


def test_zero_length_is_empty():
    _require_any_rng()
    seed = os.urandom(32)
    out = _one_shot(seed, 0)
    assert out == b""


@pytest.mark.parametrize(("n1", "n2"), [(1, 31), (16, 16), (7, 57), (64, 64)])
def test_chunking_matches_single_shot(n1: int, n2: int):
    if not STATEFUL_CTOR:
        pytest.skip("Stateful RNG API not found; skipping chunking test")

    seed = b"\x42" * 32
    readA = STATEFUL_CTOR(seed)
    part1 = readA(n1)
    part2 = readA(n2)
    combined = part1 + part2

    single = _one_shot(seed, n1 + n2)
    assert combined == single


def test_reinit_repeats_sequence_prefix():
    if not STATEFUL_CTOR:
        pytest.skip("Stateful RNG API not found; skipping reinit test")
    seed = os.urandom(32)
    n = 80

    # First RNG: take a prefix
    read1 = STATEFUL_CTOR(seed)
    prefix = read1(40)

    # Fresh RNG with same seed should reproduce the same first 40 bytes
    read2 = STATEFUL_CTOR(seed)
    prefix_again = read2(40)
    assert prefix_again == prefix

    # And the full one-shot should start with that prefix too
    all_once = _one_shot(seed, n)
    assert all_once.startswith(prefix)
