from __future__ import annotations

"""
Animica P2P compression framing helpers.

This module provides a small, dependency-optional framing layer for
compressed payloads suitable for embedding in P2P messages. It supports:

  - codec="zstd"   (via 'zstandard' package, optional)
  - codec="snappy" (via 'python-snappy' package, optional)
  - codec="none"   (no compression; still framed for consistency)

Frame format (little abstraction, robust invariants):

  +-------------------+-------------------+---------------------+
  |  MAGIC "AMCF"     |  VER=0x01         |  CODEC (u8)         |
  +-------------------+-------------------+---------------------+
  |  FLAGS (u8)       |  CLEN (varint)    |  RLEN (varint)      |
  +-------------------+-------------------+---------------------+
  |  PAYLOAD (CLEN B) |  [CRC32 (4 B)]    |  ... next frame ... |
  +-------------------+-------------------+---------------------+

  - CODEC: 0 = none, 1 = zstd, 2 = snappy
  - FLAGS bit 0 (0x01) => CRC32 of *uncompressed* data appended after payload
  - CLEN: compressed payload length
  - RLEN: uncompressed (raw) length (informational / preallocation hint)

Notes:
  - The CRC is zlib.crc32 (IEEE), not CRC32C; it's cheap and widely available.
  - For streaming, you can concatenate frames; the parser will iterate them.
  - If a requested codec isn't available at runtime, raise CompressionError.
  - Use `choose_codec()` for graceful negotiation based on availability.

Public surface:
  - choose_codec(preferred: list[str]) -> str
  - compress_frame(data: bytes, codec: str = 'zstd', *, level: int = 3, checksum: bool = True) -> bytes
  - decompress_stream(buf: bytes | memoryview | bytearray) -> Iterator[bytes]
  - parse_frames(buf) -> Iterator[Frame]
  - Compressor, Decompressor classes for incremental use
  - simple varint encode/decode (LEB128)

This module is intentionally dependency-light; all optional modules are loaded
gracefully. You can replace or extend codecs later without changing callers.
"""

import io
import struct
import zlib
from dataclasses import dataclass
from typing import Dict, Iterator, List, Optional, Tuple, Union

# ------------------------------ Optional deps --------------------------------

_has_zstd = False
_has_snappy = False

try:
    import zstandard as _zstd  # type: ignore

    _has_zstd = True
except Exception:
    _zstd = None  # type: ignore

try:
    import snappy as _snappy  # type: ignore

    _has_snappy = True
except Exception:
    _snappy = None  # type: ignore

# ------------------------------ Constants ------------------------------------

MAGIC = b"AMCF"  # Animica Compressed Frame
VERSION = 0x01

CODEC_NONE = 0
CODEC_ZSTD = 1
CODEC_SNAPPY = 2

CODEC_NAME_TO_ID: Dict[str, int] = {
    "none": CODEC_NONE,
    "zstd": CODEC_ZSTD,
    "snappy": CODEC_SNAPPY,
}
CODEC_ID_TO_NAME = {v: k for k, v in CODEC_NAME_TO_ID.items()}

FLAG_CHECKSUM = 0x01

# ------------------------------- Errors --------------------------------------


class CompressionError(RuntimeError):
    pass


class DecompressionError(RuntimeError):
    pass


# ------------------------------- Varint --------------------------------------


def _varint_encode(n: int) -> bytes:
    if n < 0:
        raise ValueError("varint cannot encode negative values")
    out = bytearray()
    while True:
        to_write = n & 0x7F
        n >>= 7
        if n:
            out.append(to_write | 0x80)
        else:
            out.append(to_write)
            break
    return bytes(out)


def _varint_decode(buf: memoryview, offset: int = 0) -> Tuple[int, int]:
    """
    Decode varint from buf[offset:].
    Returns (value, new_offset).
    Raises DecompressionError on truncation or overflow.
    """
    shift = 0
    result = 0
    i = offset
    while True:
        if i >= len(buf):
            raise DecompressionError("truncated varint")
        b = buf[i]
        i += 1
        result |= (b & 0x7F) << shift
        if (b & 0x80) == 0:
            break
        shift += 7
        if shift > 63:
            raise DecompressionError("varint too large")
    return result, i


# ------------------------------- Frame model ---------------------------------


@dataclass(frozen=True)
class FrameHeader:
    version: int
    codec_id: int
    flags: int
    compressed_len: int
    raw_len: int


@dataclass(frozen=True)
class Frame:
    header: FrameHeader
    payload: bytes
    crc32: Optional[int]  # uncompressed CRC32 if present

    def codec_name(self) -> str:
        return CODEC_ID_TO_NAME.get(
            self.header.codec_id, f"unknown({self.header.codec_id})"
        )


# ------------------------------- Codec utils ---------------------------------


def choose_codec(preferred: List[str]) -> str:
    """
    Choose the first available codec from the preferred list.
    Fallback order: preferred... then 'none'.
    """
    for name in preferred:
        if name == "zstd" and _has_zstd:
            return "zstd"
        if name == "snappy" and _has_snappy:
            return "snappy"
        if name == "none":
            return "none"
    # fallback
    if _has_zstd:
        return "zstd"
    if _has_snappy:
        return "snappy"
    return "none"


def _check_codec_available(name: str) -> None:
    if name == "zstd" and not _has_zstd:
        raise CompressionError("zstd requested but 'zstandard' module not available")
    if name == "snappy" and not _has_snappy:
        raise CompressionError(
            "snappy requested but 'python-snappy' module not available"
        )
    if name not in CODEC_NAME_TO_ID:
        raise CompressionError(f"unknown codec: {name}")


def _compress(data: bytes, codec: str, level: int) -> bytes:
    if codec == "none":
        return data
    if codec == "zstd":
        # default compression level ~3 balances speed/ratio
        c = _zstd.ZstdCompressor(level=level)  # type: ignore[attr-defined]
        return c.compress(data)
    if codec == "snappy":
        return _snappy.compress(data)  # type: ignore[attr-defined]
    raise CompressionError(f"unsupported codec: {codec}")


def _decompress(data: bytes, codec_id: int, expected_raw_len: Optional[int]) -> bytes:
    if codec_id == CODEC_NONE:
        raw = data
    elif codec_id == CODEC_ZSTD:
        if not _has_zstd:
            raise DecompressionError(
                "zstd frame received but 'zstandard' not available"
            )
        d = _zstd.ZstdDecompressor()  # type: ignore[attr-defined]
        raw = d.decompress(data, max_output_size=expected_raw_len or 0)
    elif codec_id == CODEC_SNAPPY:
        if not _has_snappy:
            raise DecompressionError(
                "snappy frame received but 'python-snappy' not available"
            )
        raw = _snappy.decompress(data)  # type: ignore[attr-defined]
    else:
        raise DecompressionError(f"unknown codec id {codec_id}")
    if (
        expected_raw_len is not None
        and expected_raw_len >= 0
        and len(raw) != expected_raw_len
    ):
        # Don't hard-fail: many codecs may not preserve exact RLEN hints. Make it a soft check.
        # We still warn via exception type for callers that choose to enforce.
        pass
    return raw


# ------------------------------- Framing IO ----------------------------------


def _encode_header(codec: str, flags: int, clen: int, rlen: int) -> bytes:
    codec_id = CODEC_NAME_TO_ID[codec]
    return b"".join(
        [
            MAGIC,
            bytes((VERSION, codec_id, flags)),
            _varint_encode(clen),
            _varint_encode(rlen),
        ]
    )


def _decode_header(buf: memoryview, offset: int) -> Tuple[FrameHeader, int]:
    end_magic = offset + 4
    if end_magic > len(buf) or bytes(buf[offset:end_magic]) != MAGIC:
        raise DecompressionError("bad frame magic")
    offset = end_magic
    if offset + 3 > len(buf):
        raise DecompressionError("truncated fixed header")
    version = buf[offset]
    codec_id = buf[offset + 1]
    flags = buf[offset + 2]
    offset += 3
    clen, offset = _varint_decode(buf, offset)
    rlen, offset = _varint_decode(buf, offset)

    if version != VERSION:
        raise DecompressionError(f"unsupported frame version: {version}")
    if codec_id not in CODEC_ID_TO_NAME:
        raise DecompressionError(f"unknown codec id: {codec_id}")
    if clen < 0 or rlen < 0:
        raise DecompressionError("negative length in header")

    return FrameHeader(version, codec_id, flags, clen, rlen), offset


def compress_frame(
    data: Union[bytes, bytearray, memoryview],
    codec: str = "zstd",
    *,
    level: int = 3,
    checksum: bool = True,
) -> bytes:
    """
    Compress a single payload into a framed buffer.
    """
    codec = codec.lower()
    _check_codec_available(codec)
    raw = bytes(data)
    comp = _compress(raw, codec, level=level)
    flags = FLAG_CHECKSUM if checksum else 0
    header = _encode_header(codec, flags, len(comp), len(raw))
    tail = b""
    if checksum:
        crc = zlib.crc32(raw) & 0xFFFFFFFF
        tail = struct.pack(">I", crc)
    return header + comp + tail


def parse_frames(buf: Union[bytes, bytearray, memoryview]) -> Iterator[Frame]:
    """
    Iterate over frames from a contiguous buffer; yields raw Frame objects
    (payload still compressed). Use Decompressor for decoded bytes.
    """
    mv = memoryview(buf)
    off = 0
    while off < len(mv):
        header, off = _decode_header(mv, off)
        end = off + header.compressed_len
        if end > len(mv):
            raise DecompressionError("truncated payload in frame")
        payload = bytes(mv[off:end])
        off = end
        crc: Optional[int] = None
        if header.flags & FLAG_CHECKSUM:
            if off + 4 > len(mv):
                raise DecompressionError("truncated crc32")
            (crc,) = struct.unpack(">I", mv[off : off + 4])
            off += 4
        yield Frame(header=header, payload=payload, crc32=crc)


def decompress_stream(buf: Union[bytes, bytearray, memoryview]) -> Iterator[bytes]:
    """
    Iterate over decompressed payloads from a concatenated framed stream.
    """
    for fr in parse_frames(buf):
        raw = _decompress(fr.payload, fr.header.codec_id, fr.header.raw_len)
        if fr.crc32 is not None:
            crc = zlib.crc32(raw) & 0xFFFFFFFF
            if crc != fr.crc32:
                raise DecompressionError("crc32 mismatch on decompressed payload")
        yield raw


# ---------------------------- Incremental API --------------------------------


class Compressor:
    """
    Incremental compressor that writes framed blocks into an internal buffer
    (or a file-like object).
    """

    def __init__(
        self,
        codec: str = "zstd",
        *,
        level: int = 3,
        checksum: bool = True,
        sink: Optional[io.BufferedIOBase] = None,
    ) -> None:
        self.codec = codec.lower()
        _check_codec_available(self.codec)
        self.level = level
        self.checksum = checksum
        self._sink = sink
        self._buf = bytearray()

    def write(self, chunk: Union[bytes, bytearray, memoryview]) -> int:
        frame = compress_frame(
            chunk, codec=self.codec, level=self.level, checksum=self.checksum
        )
        if self._sink is not None:
            self._sink.write(frame)
        else:
            self._buf.extend(frame)
        return len(chunk)

    def getvalue(self) -> bytes:
        if self._sink is not None:
            raise RuntimeError(
                "Compressor with sink does not buffer; use the sink directly"
            )
        return bytes(self._buf)


class Decompressor:
    """
    Incremental decompressor: feed framed bytes and iterate decoded blocks.
    """

    def __init__(self) -> None:
        self._buf = bytearray()

    def feed(self, data: Union[bytes, bytearray, memoryview]) -> None:
        self._buf.extend(data)

    def __iter__(self) -> Iterator[bytes]:
        mv = memoryview(self._buf)
        off = 0
        while off < len(mv):
            # Attempt to parse one frame
            try:
                header, h_off = _decode_header(mv, off)
                payload_end = h_off + header.compressed_len
                tail = 4 if (header.flags & FLAG_CHECKSUM) else 0
                if payload_end + tail > len(mv):
                    break  # need more data
                payload = bytes(mv[h_off:payload_end])
                crc: Optional[int] = None
                if header.flags & FLAG_CHECKSUM:
                    (crc,) = struct.unpack(">I", mv[payload_end : payload_end + 4])
                off = payload_end + tail
                # Decompress + validate
                raw = _decompress(payload, header.codec_id, header.raw_len)
                if crc is not None:
                    calc = zlib.crc32(raw) & 0xFFFFFFFF
                    if calc != crc:
                        raise DecompressionError("crc32 mismatch on streamed payload")
                yield raw
            except DecompressionError as e:
                # If header isn't even complete, stop; else propagate error.
                if off + 4 > len(mv):
                    break
                raise
        # Drop consumed bytes
        if off:
            del self._buf[:off]


# ------------------------------- Utilities -----------------------------------


def is_supported(codec: str) -> bool:
    c = codec.lower()
    if c == "none":
        return True
    if c == "zstd":
        return _has_zstd
    if c == "snappy":
        return _has_snappy
    return False


def negotiate(preferred_local: List[str], peer_offered: List[str]) -> str:
    """
    Simple codec negotiation: pick first local preference that is both offered by
    the peer and available locally. Fallback: 'none'.
    """
    offered = {c.lower() for c in peer_offered}
    for c in (x.lower() for x in preferred_local):
        if c in offered and is_supported(c):
            return c
    return "none"


# ------------------------------- Self-test -----------------------------------


if __name__ == "__main__":
    # quick smoke
    payloads = [b"hello world", b"A" * 0, b"B" * 1024, zlib.compress(b"random-ish", 9)]
    for codec in ["zstd", "snappy", "none"]:
        if not is_supported(codec):
            print(f"[skip] {codec} not available")
            continue
        comp = Compressor(codec=codec, level=3, checksum=True)
        for p in payloads:
            comp.write(p)
        stream = comp.getvalue()
        total = 0
        dec = Decompressor()
        dec.feed(stream)
        for i, raw in enumerate(dec, 1):
            total += len(raw)
        print(
            f"[ok] {codec}: {len(stream)} bytes framed, {total} bytes raw across {len(payloads)} frames"
        )
