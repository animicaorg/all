from __future__ import annotations

from typing import Union


BytesLike = Union[bytes, bytearray, memoryview]


def _to_bytes(blob: BytesLike) -> bytes:
    """
    Normalize any supported bytes-like input into a plain `bytes` object.
    """
    if isinstance(blob, bytes):
        return blob
    if isinstance(blob, bytearray):
        return bytes(blob)
    if isinstance(blob, memoryview):
        return blob.tobytes()
    raise TypeError(f"blob must be bytes-like, got {type(blob)!r}")


def chunk_blob(blob: BytesLike, *, chunk_size: int) -> list[bytes]:
    """
    Deterministically split `blob` into fixed-size chunks.

    Rules:
      - `chunk_size` must be > 0, otherwise ValueError is raised.
      - Empty blob → returns [] (no chunks).
      - For non-empty blobs:
          * All chunks are non-empty.
          * All chunks have length <= chunk_size.
          * Concatenating all chunks yields the original blob.
      - No randomness; same input → same chunk boundaries.
    """
    if chunk_size <= 0:
        raise ValueError(f"chunk_size must be positive, got {chunk_size}")

    data = _to_bytes(blob)

    if not data:
        # Design choice: represent empty blob as "no chunks" instead of
        # a single empty chunk.
        return []

    length = len(data)
    chunks: list[bytes] = []

    # Simple deterministic slicing, last chunk may be shorter.
    for pos in range(0, length, chunk_size):
        chunk = data[pos : pos + chunk_size]
        # Safety: ensure we never emit a zero-length chunk.
        if not chunk:
            continue
        chunks.append(chunk)

    return chunks
