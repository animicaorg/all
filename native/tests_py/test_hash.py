import os
import random
from typing import Any, Callable, Iterable, Optional, Tuple

import pytest
# Python reference implementations
# Must provide: blake3_hash(bytes)->bytes|hexstr, keccak256(..), sha256(..)
from omni.utils import hashing as pyref  # type: ignore

# Shared helpers and native handle from the package __init__
from . import SKIP_HEAVY, TEST_SEED, is_ci, native  # type: ignore

# ---------- Utilities ----------


def _to_bytes(x: Any) -> bytes:
    """Normalize digest outputs that may be bytes, bytearray, memoryview, or hex string."""
    if isinstance(x, (bytes, bytearray, memoryview)):
        return bytes(x)
    if isinstance(x, str):
        s = x[2:] if x.startswith("0x") else x
        # tolerate odd-length hex strings by left-padding
        if len(s) % 2:
            s = "0" + s
        return bytes.fromhex(s)
    raise TypeError(f"Unsupported digest output type: {type(x)}")


def _rand(seed: Optional[int]) -> random.Random:
    # Deterministic RNG if seed provided, otherwise system-randomized seed
    if seed is None:
        # derive a seed from os.urandom for reproducibility within a single run
        seed = int.from_bytes(os.urandom(8), "little")
    return random.Random(seed)


def _gen_data(pattern: str, size: int, rng: random.Random) -> bytes:
    if pattern == "zeros":
        return b"\x00" * size
    if pattern == "ones":
        return b"\xff" * size
    if pattern == "inc":
        return bytes((i % 256 for i in range(size)))
    if pattern == "random":
        return bytes(rng.getrandbits(8) for _ in range(size))
    raise ValueError(f"unknown pattern {pattern}")


def _chunkify(b: bytes, rng: random.Random) -> Iterable[bytes]:
    """Split into variable chunk sizes to exercise streaming paths."""
    i = 0
    n = len(b)
    while i < n:
        # Favor small-to-medium chunks, sometimes large
        step = min(n - i, max(1, int(rng.expovariate(1 / 64))))
        yield b[i : i + step]
        i += step


# ---------- Param spaces ----------

SMALL_SIZES = [0, 1, 3, 7, 32, 64, 65, 127, 128, 255, 256, 1000, 4096]
HEAVY_SIZES = [128 * 1024, 512 * 1024, 1 * 1024 * 1024]  # up to 1 MiB

PATTERNS = ["zeros", "ones", "inc", "random"]


# ---------- One-shot parity tests ----------


@pytest.mark.parametrize(
    "size", SMALL_SIZES + ([] if SKIP_HEAVY or is_ci() else HEAVY_SIZES)
)
@pytest.mark.parametrize("pattern", PATTERNS)
def test_blake3_parity(pattern: str, size: int):
    rng = _rand(TEST_SEED)
    data = _gen_data(pattern, size, rng)

    want = _to_bytes(pyref.blake3_hash(data))
    got = _to_bytes(native.blake3_hash(data))

    assert got == want, f"blake3 mismatch for pattern={pattern} size={size}"


@pytest.mark.parametrize(
    "size", SMALL_SIZES + ([] if SKIP_HEAVY or is_ci() else HEAVY_SIZES)
)
@pytest.mark.parametrize("pattern", PATTERNS)
def test_keccak256_parity(pattern: str, size: int):
    rng = _rand(TEST_SEED)
    data = _gen_data(pattern, size, rng)

    want = _to_bytes(pyref.keccak256(data))
    got = _to_bytes(native.keccak256(data))

    assert got == want, f"keccak256 mismatch for pattern={pattern} size={size}"


@pytest.mark.parametrize(
    "size", SMALL_SIZES + ([] if SKIP_HEAVY or is_ci() else HEAVY_SIZES)
)
@pytest.mark.parametrize("pattern", PATTERNS)
def test_sha256_parity(pattern: str, size: int):
    rng = _rand(TEST_SEED)
    data = _gen_data(pattern, size, rng)

    want = _to_bytes(pyref.sha256(data))
    got = _to_bytes(native.sha256(data))

    assert got == want, f"sha256 mismatch for pattern={pattern} size={size}"


# ---------- Input type coverage (bytes-like coercions) ----------


@pytest.mark.parametrize("factory", [bytes, bytearray, memoryview])
def test_input_types_are_accepted(factory: Callable[[bytes], Any]):
    data = factory(b"animica: bytes-like input acceptance")
    # If memoryview or bytearray was provided, ensure native functions can handle them
    b3 = _to_bytes(native.blake3_hash(data))
    k = _to_bytes(native.keccak256(data))
    s2 = _to_bytes(native.sha256(data))

    # Cross-check against reference to ensure equality, not just acceptance
    assert b3 == _to_bytes(pyref.blake3_hash(bytes(data)))
    assert k == _to_bytes(pyref.keccak256(bytes(data)))
    assert s2 == _to_bytes(pyref.sha256(bytes(data)))


# ---------- Streaming vs one-shot (if available) ----------


def _has_streaming() -> Tuple[bool, bool, bool]:
    """Detect whether streaming contexts are available on the native module."""
    return (
        hasattr(native, "Blake3"),
        hasattr(native, "Keccak256"),
        hasattr(native, "Sha256"),
    )


@pytest.mark.parametrize("pattern", PATTERNS)
@pytest.mark.parametrize("size", [0, 1, 31, 32, 33, 255, 4096, 16384])
def test_streaming_matches_one_shot_blake3(pattern: str, size: int):
    have_blake3, _, _ = _has_streaming()
    if not have_blake3:
        pytest.skip("native.Blake3 streaming context not available")
    rng = _rand(TEST_SEED)
    data = _gen_data(pattern, size, rng)

    want = _to_bytes(native.blake3_hash(data))

    ctx = native.Blake3()
    for chunk in _chunkify(data, rng):
        ctx.update(chunk)
    got = _to_bytes(ctx.digest())

    assert got == want, f"Blake3 streaming != one-shot (pattern={pattern}, size={size})"


@pytest.mark.parametrize("pattern", PATTERNS)
@pytest.mark.parametrize("size", [0, 1, 31, 32, 33, 255, 4096, 16384])
def test_streaming_matches_one_shot_keccak(pattern: str, size: int):
    _, have_keccak, _ = _has_streaming()
    if not have_keccak:
        pytest.skip("native.Keccak256 streaming context not available")
    rng = _rand(TEST_SEED)
    data = _gen_data(pattern, size, rng)

    want = _to_bytes(native.keccak256(data))

    ctx = native.Keccak256()
    for chunk in _chunkify(data, rng):
        ctx.update(chunk)
    got = _to_bytes(ctx.digest())

    assert (
        got == want
    ), f"Keccak256 streaming != one-shot (pattern={pattern}, size={size})"


@pytest.mark.parametrize("pattern", PATTERNS)
@pytest.mark.parametrize("size", [0, 1, 31, 32, 33, 255, 4096, 16384])
def test_streaming_matches_one_shot_sha256(pattern: str, size: int):
    _, _, have_sha256 = _has_streaming()
    if not have_sha256:
        pytest.skip("native.Sha256 streaming context not available")
    rng = _rand(TEST_SEED)
    data = _gen_data(pattern, size, rng)

    want = _to_bytes(native.sha256(data))

    ctx = native.Sha256()
    for chunk in _chunkify(data, rng):
        ctx.update(chunk)
    got = _to_bytes(ctx.digest())

    assert got == want, f"Sha256 streaming != one-shot (pattern={pattern}, size={size})"


# ---------- Known test vectors (sanity) ----------


@pytest.mark.parametrize(
    "msg, b3_hex, k_hex, s_hex",
    [
        (
            "",  # empty string
            "0e5751c026e543b2e8ab2eb06099daa1f7760f36b0e9aaf3d8fba9e0e1c9e5a6",
            "c5d2460186f7233c927e7db2dcc703c0e500b653ca82273b7bfad8045d85a470",
            "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
        ),
        (
            "abc",
            "9f4e1e02d00e8ab0a3e5ae62b2cf9ce0eec2206f4320d06c938753cfc1e66edd",
            "4e03657aea45a94fc7d47ba826c8d667c0d1e6e33a64a036ec44f58fa12d6c45",
            "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad",
        ),
    ],
)
def test_known_vectors(msg: str, b3_hex: str, k_hex: str, s_hex: str):
    data = msg.encode()
    assert _to_bytes(native.blake3_hash(data)) == bytes.fromhex(b3_hex)
    assert _to_bytes(native.keccak256(data)) == bytes.fromhex(k_hex)
    assert _to_bytes(native.sha256(data)) == bytes.fromhex(s_hex)
