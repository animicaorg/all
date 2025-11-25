"""
Animica • DA • Blob Chunker

Stream-friendly helpers to iterate over payloads as fixed-size chunks.

Goals
-----
- Zero-copy slices **when possible** (bytes/bytearray/memoryview sources).
- Support file paths / file-like objects / byte iterables for streaming.
- Provide rich `Chunk` objects carrying offsets and last-chunk flag.
- Small, dependency-free, and safe to use in pipelines (encoder/NMT).

Notes on zero-copy
------------------
• For in-memory sources (bytes/bytearray/memoryview), we return *sliced*
  `memoryview` objects that reference the original buffer: no extra copy.

• For file-like sources, each `read()` produces a new bytes object created by
  the OS/file layer. We wrap that in a `memoryview` to avoid an extra copy,
  but the read itself necessarily materializes data.

API
---
- Chunk: dataclass(idx, offset, data: memoryview, is_last: bool)
- iter_chunks(source, chunk_size, *, max_bytes=None, start=0)
- chunk_bytes(b, chunk_size, *, max_bytes=None, start=0)
- chunk_file(path_or_file, chunk_size, *, max_bytes=None, start=0)
- chunk_iter(byte_iterable, chunk_size, *, max_bytes=None)

All iterators yield `Chunk` in order (idx starting at 0). The `data` field
is a `memoryview` that you should consume before moving on if you rely on its
backing storage (e.g., to avoid holding onto large originals).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Generator, Iterable, IO, Optional, Union, overload
import io
import os


@dataclass(frozen=True)
class Chunk:
    """A single contiguous chunk from a larger payload."""
    idx: int
    offset: int
    data: memoryview
    is_last: bool

    def __len__(self) -> int:
        return len(self.data)


# --------------------------------------------------------------------------- #
# Core dispatcher
# --------------------------------------------------------------------------- #

Source = Union[bytes, bytearray, memoryview, str, os.PathLike, IO[bytes], Iterable[bytes]]


def iter_chunks(
    source: Source,
    chunk_size: int,
    *,
    max_bytes: Optional[int] = None,
    start: int = 0,
) -> Generator[Chunk, None, None]:
    """
    Iterate over `source` yielding fixed-size (last may be smaller) chunks.

    Args:
      source: bytes-like, filepath, file-like (rb), or iterable of bytes.
      chunk_size: desired chunk size in bytes (>0).
      max_bytes: optional cap on total bytes to read from `start`.
      start: byte offset to start from (only for bytes-like sources).

    Yields:
      Chunk(idx, offset, data: memoryview, is_last)

    Raises:
      ValueError on invalid arguments.
    """
    if chunk_size <= 0:
        raise ValueError("chunk_size must be > 0")

    # In-memory zero-copy path
    if isinstance(source, (bytes, bytearray, memoryview)):
        yield from _chunk_byteslike(source, chunk_size, max_bytes=max_bytes, start=start)
        return

    # File path → open
    if isinstance(source, (str, os.PathLike)):
        with open(source, "rb") as f:
            yield from _chunk_filelike(f, chunk_size, max_bytes=max_bytes)
        return

    # File-like object
    if hasattr(source, "read"):
        # Mypy/typing: treat as IO[bytes]
        yield from _chunk_filelike(source, chunk_size, max_bytes=max_bytes)
        return

    # Iterable of bytes (e.g., network stream or custom producer)
    if isinstance(source, Iterable):
        yield from _chunk_iterable(source, chunk_size, max_bytes=max_bytes)
        return

    raise TypeError(f"Unsupported source type: {type(source)!r}")


# --------------------------------------------------------------------------- #
# Implementations
# --------------------------------------------------------------------------- #

def _chunk_byteslike(
    buf: Union[bytes, bytearray, memoryview],
    chunk_size: int,
    *,
    max_bytes: Optional[int],
    start: int,
) -> Generator[Chunk, None, None]:
    mv = memoryview(buf)
    if start < 0 or start > len(mv):
        raise ValueError("start out of range")
    total_end = len(mv) if max_bytes is None else min(len(mv), start + max_bytes)
    idx = 0
    off = start
    while off < total_end:
        end = min(off + chunk_size, total_end)
        # Zero-copy slice
        view = mv[off:end]
        yield Chunk(idx=idx, offset=off, data=view, is_last=(end >= total_end))
        idx += 1
        off = end


def _chunk_filelike(
    f: IO[bytes],
    chunk_size: int,
    *,
    max_bytes: Optional[int],
) -> Generator[Chunk, None, None]:
    # If the file is seekable and max_bytes is provided, we can stop early precisely.
    remaining = max_bytes if max_bytes is not None else None
    idx = 0
    offset = _tell_safe(f)

    while True:
        if remaining is not None and remaining <= 0:
            break
        n = chunk_size if remaining is None else min(chunk_size, remaining)
        b = f.read(n)
        if not b:
            # EOF
            if idx == 0:
                # empty stream → yield nothing
                return
            # last yielded chunk already had is_last computed; nothing to do
            return
        mv = memoryview(b)  # no extra copy; wraps bytes object from read()
        next_offset = offset + len(mv)
        # Peek if we will have more data to set is_last correctly
        is_last = False
        if remaining is not None:
            remaining -= len(mv)
            is_last = remaining <= 0

        yield Chunk(idx=idx, offset=offset, data=mv, is_last=is_last)
        idx += 1
        offset = next_offset


def _chunk_iterable(
    it: Iterable[bytes],
    chunk_size: int,
    *,
    max_bytes: Optional[int],
) -> Generator[Chunk, None, None]:
    """
    Accepts an iterable of arbitrary-sized byte strings and re-chunks it into
    fixed-size pieces. Uses a small internal buffer; emitted views reference
    immutable bytes for safety (each piece gets its own backing).
    """
    buf = bytearray()
    idx = 0
    offset = 0
    remaining = max_bytes if max_bytes is not None else None

    def take_from_buf(n: int) -> memoryview:
        # Pop n bytes from the front of buffer without extra copy of the slice.
        # We create a new bytes from the popped region to decouple from the buffer's
        # future growth/shrinks (and to ensure immutability for consumers).
        out = bytes(buf[:n])
        del buf[:n]
        return memoryview(out)

    for piece in it:
        if not piece:
            continue
        if remaining is not None and remaining <= 0:
            break
        if remaining is not None:
            piece = piece[:remaining]
        buf += piece
        if remaining is not None:
            remaining -= len(piece)

        while len(buf) >= chunk_size:
            mv = take_from_buf(chunk_size)
            # If remaining == 0 here we still may have buffered extra, but we respect max_bytes.
            yield Chunk(idx=idx, offset=offset, data=mv, is_last=(remaining == 0 and len(buf) == 0))
            idx += 1
            offset += len(mv)

    # Flush remainder
    if (remaining is None or remaining > 0) and len(buf) > 0:
        n = len(buf) if remaining is None else min(len(buf), remaining)
        if n > 0:
            mv = take_from_buf(n)
            yield Chunk(idx=idx, offset=offset, data=mv, is_last=True)


# --------------------------------------------------------------------------- #
# Convenience wrappers
# --------------------------------------------------------------------------- #

def chunk_bytes(
    data: Union[bytes, bytearray, memoryview],
    chunk_size: int,
    *,
    max_bytes: Optional[int] = None,
    start: int = 0,
) -> Generator[Chunk, None, None]:
    """Zero-copy chunking over in-memory data."""
    return _chunk_byteslike(data, chunk_size, max_bytes=max_bytes, start=start)


def chunk_file(
    path_or_file: Union[str, os.PathLike, IO[bytes]],
    chunk_size: int,
    *,
    max_bytes: Optional[int] = None,
) -> Generator[Chunk, None, None]:
    """Chunk from a file path or already-open file object."""
    if isinstance(path_or_file, (str, os.PathLike)):
        with open(path_or_file, "rb") as f:
            yield from _chunk_filelike(f, chunk_size, max_bytes=max_bytes)
    else:
        yield from _chunk_filelike(path_or_file, chunk_size, max_bytes=max_bytes)


def chunk_iter(
    byte_iterable: Iterable[bytes],
    chunk_size: int,
    *,
    max_bytes: Optional[int] = None,
) -> Generator[Chunk, None, None]:
    """Re-chunk an iterable of arbitrary-sized byte strings."""
    return _chunk_iterable(byte_iterable, chunk_size, max_bytes=max_bytes)


# --------------------------------------------------------------------------- #
# Utilities
# --------------------------------------------------------------------------- #

def _tell_safe(f: IO[bytes]) -> int:
    try:
        return f.tell()  # type: ignore[attr-defined]
    except Exception:
        return 0


# --------------------------------------------------------------------------- #
# Self-test
# --------------------------------------------------------------------------- #

if __name__ == "__main__":  # pragma: no cover
    payload = b"abcdefghijklmnopqrstuvwxyz"
    print("bytes-like:")
    for c in chunk_bytes(payload, 5):
        print(c.idx, c.offset, bytes(c.data), c.is_last)

    print("\nfile-like:")
    bio = io.BytesIO(payload)
    for c in chunk_file(bio, 7):
        print(c.idx, c.offset, bytes(c.data), c.is_last)

    print("\niterable:")
    parts = [b"abc", b"defghij", b"kl", b"mnopqr", b"stuvwxyz"]
    for c in chunk_iter(parts, 4):
        print(c.idx, c.offset, bytes(c.data), c.is_last)
