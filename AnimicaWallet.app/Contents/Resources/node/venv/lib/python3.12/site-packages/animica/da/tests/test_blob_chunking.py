from __future__ import annotations

import pathlib
import random
import sys

import pytest

# Make sure the local "python/animica" package is importable when running
# tests from the repo root.
THIS_FILE = pathlib.Path(__file__).resolve()
# .../python/animica/da/tests/test_blob_chunking.py
# parents[0] = tests, [1] = da, [2] = animica, [3] = python
PYTHON_ROOT = THIS_FILE.parents[3]
if str(PYTHON_ROOT) not in sys.path:
    sys.path.insert(0, str(PYTHON_ROOT))

from animica.da.blob_chunking import chunk_blob


def _reassemble(chunks: list[bytes]) -> bytes:
    """Helper to reconstruct the blob from chunks."""
    return b"".join(chunks)


@pytest.mark.parametrize("chunk_size", [1, 7, 32, 256])
def test_empty_blob_produces_no_chunks(chunk_size: int) -> None:
    chunks = chunk_blob(b"", chunk_size=chunk_size)

    # Design choice: for an empty blob we expect *no* chunks.
    assert isinstance(chunks, list)
    assert chunks == []


@pytest.mark.parametrize("chunk_size", [1, 7, 32, 256])
def test_single_byte_and_small_blobs(chunk_size: int) -> None:
    # 1-byte blob
    blob = b"a"
    chunks = chunk_blob(blob, chunk_size=chunk_size)
    assert len(chunks) == 1
    assert chunks[0] == blob
    assert 0 < len(chunks[0]) <= chunk_size

    # Exactly chunk_size bytes
    blob2 = b"b" * chunk_size
    chunks2 = chunk_blob(blob2, chunk_size=chunk_size)
    assert len(chunks2) == 1
    assert chunks2[0] == blob2
    assert len(chunks2[0]) == chunk_size

    # chunk_size + 1 bytes
    blob3 = b"c" * (chunk_size + 1)
    chunks3 = chunk_blob(blob3, chunk_size=chunk_size)
    assert len(chunks3) >= 2

    # No chunk should exceed chunk_size and no zero-length chunks.
    for ch in chunks3:
        assert isinstance(ch, (bytes, bytearray))
        assert 0 < len(ch) <= chunk_size

    # Reassembly should yield the original payload (no corruption)
    assert _reassemble(chunks3) == blob3


@pytest.mark.parametrize("chunk_size", [64, 256, 1024])
def test_large_blob_chunking_invariants(chunk_size: int) -> None:
    # Use deterministic RNG so failures are reproducible.
    rnd = random.Random(42)
    length = 123_456
    blob = bytes(rnd.getrandbits(8) for _ in range(length))

    chunks = chunk_blob(blob, chunk_size=chunk_size)

    # Non-empty input must yield at least one chunk.
    assert len(chunks) > 0

    # All chunks obey size bound; no zero-length chunks.
    for i, ch in enumerate(chunks):
        assert isinstance(ch, (bytes, bytearray)), f"chunk {i} is not bytes-like"
        assert 0 < len(ch) <= chunk_size, f"chunk {i} has invalid length {len(ch)}"

    # Concatenation must yield the original blob (no data loss or corruption).
    reconstructed = _reassemble(chunks)
    assert reconstructed == blob


@pytest.mark.parametrize("chunk_size", [33, 128, 4096])
def test_chunking_is_deterministic_for_same_input(chunk_size: int) -> None:
    rnd = random.Random(123)
    blob = bytes(rnd.getrandbits(8) for _ in range(10_000))

    chunks1 = chunk_blob(blob, chunk_size=chunk_size)
    chunks2 = chunk_blob(blob, chunk_size=chunk_size)

    # Deterministic: exact same chunk boundaries and contents.
    assert chunks1 == chunks2

    # And still reconstructs correctly.
    assert _reassemble(chunks1) == blob
