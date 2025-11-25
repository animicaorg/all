from __future__ import annotations

"""
Animica • DA • Retrieval • Handlers

Request/response helpers used by the DA retrieval API:

- Commitment parsing and ETag helpers
- HTTP Range parsing ("bytes=...") and validation
- Streaming file responses with correct headers (206/200)
- JSON responses with cache headers
- Small utilities for content types and filenames

These helpers are framework-friendly (FastAPI/Starlette) but avoid importing
router objects directly, so the module can be used by tests and services.
"""

import os
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Iterator, Optional, Tuple

from fastapi import HTTPException, Request, status
from fastapi.responses import JSONResponse, StreamingResponse

# Optional dependency: hex helpers from da.utils.bytes (fallback to local impl)
try:
    from da.utils.bytes import hex_to_bytes, bytes_to_hex  # type: ignore
except Exception:  # pragma: no cover
    def hex_to_bytes(s: str) -> bytes:
        s = s.lower().strip()
        if s.startswith("0x"):
            s = s[2:]
        if len(s) % 2:
            s = "0" + s
        return bytes.fromhex(s)

    def bytes_to_hex(b: bytes, prefix: bool = True) -> str:
        h = b.hex()
        return "0x" + h if prefix else h


# --------------------------------------------------------------------------------------
# Commitment parsing & ETag helpers
# --------------------------------------------------------------------------------------

def normalize_commitment_hex(s: str) -> str:
    """
    Normalize a commitment hex string:
    - strip spaces
    - allow optional 0x prefix
    - lower-case
    - validate hex chars and length (>= 32 bytes preferred but not enforced here)
    """
    if not isinstance(s, str) or not s:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Missing commitment")
    s2 = s.strip().lower()
    if s2.startswith("0x"):
        s2 = s2[2:]
    if not re.fullmatch(r"[0-9a-f]+", s2 or ""):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid commitment (non-hex)")
    # Typical commitment size is 32 bytes (64 hex). We accept any >= 16 bytes to be future-proof.
    if len(s2) < 32:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Commitment too short")
    return s2

def etag_for_commitment(commitment_hex: str) -> str:
    """
    Build a strong ETag for a DA artifact bound to the commitment.
    """
    ch = normalize_commitment_hex(commitment_hex)
    return f"\"da-{ch}\""


# --------------------------------------------------------------------------------------
# Range parsing and representation
# --------------------------------------------------------------------------------------

_BYTES_UNIT = "bytes="

@dataclass(frozen=True)
class RangeSpec:
    start: int        # inclusive
    end: int          # inclusive
    length: int       # total object length

    @property
    def size(self) -> int:
        return self.end - self.start + 1

    def to_content_range(self) -> str:
        return f"bytes {self.start}-{self.end}/{self.length}"

def parse_range_header(range_header: Optional[str], total_length: int) -> Optional[RangeSpec]:
    """
    Parse a single-range header of the form:
        bytes=START-
        bytes=START-END
        bytes=-SUFFIX
    Returns None if no Range provided (caller should send 200 full).
    Raises 416 if unsatisfiable or malformed.

    We only support a *single* range. If multiple ranges are requested, raise 416.
    """
    if range_header is None:
        return None

    hdr = range_header.strip().replace(" ", "")
    if not hdr:
        return None
    if not hdr.startswith(_BYTES_UNIT):
        # Unknown unit — reject
        raise HTTPException(status_code=status.HTTP_416_REQUESTED_RANGE_NOT_SATISFIABLE,
                            detail="Unsupported Range unit")

    # Reject multiple ranges "bytes=a-b,c-d"
    parts = hdr[len(_BYTES_UNIT):].split(",")
    if len(parts) != 1:
        raise HTTPException(status_code=status.HTTP_416_REQUESTED_RANGE_NOT_SATISFIABLE,
                            detail="Multiple ranges not supported")
    spec = parts[0]

    # "-SUFFIX" (last N bytes)
    if spec.startswith("-"):
        try:
            n = int(spec[1:])
        except ValueError:
            raise HTTPException(status_code=status.HTTP_416_REQUESTED_RANGE_NOT_SATISFIABLE,
                                detail="Invalid Range header")
        if n <= 0:
            raise HTTPException(status_code=status.HTTP_416_REQUESTED_RANGE_NOT_SATISFIABLE,
                                detail="Invalid suffix length")
        if total_length == 0:
            raise HTTPException(status_code=status.HTTP_416_REQUESTED_RANGE_NOT_SATISFIABLE,
                                detail="Empty resource")
        n = min(n, total_length)
        start = total_length - n
        end = total_length - 1
        return RangeSpec(start=start, end=end, length=total_length)

    # "START-" or "START-END"
    if "-" not in spec:
        raise HTTPException(status_code=status.HTTP_416_REQUESTED_RANGE_NOT_SATISFIABLE,
                            detail="Invalid Range (missing '-')")
    start_s, end_s = spec.split("-", 1)
    try:
        start = int(start_s)
    except ValueError:
        raise HTTPException(status_code=status.HTTP_416_REQUESTED_RANGE_NOT_SATISFIABLE,
                            detail="Invalid Range start")
    if start < 0 or start >= total_length:
        raise HTTPException(status_code=status.HTTP_416_REQUESTED_RANGE_NOT_SATISFIABLE,
                            detail="Range start out of bounds")

    if end_s == "" or end_s is None:
        end = total_length - 1
    else:
        try:
            end = int(end_s)
        except ValueError:
            raise HTTPException(status_code=status.HTTP_416_REQUESTED_RANGE_NOT_SATISFIABLE,
                                detail="Invalid Range end")
        if end < start:
            raise HTTPException(status_code=status.HTTP_416_REQUESTED_RANGE_NOT_SATISFIABLE,
                                detail="Range end < start")
        end = min(end, total_length - 1)

    return RangeSpec(start=start, end=end, length=total_length)


# --------------------------------------------------------------------------------------
# Streaming and JSON response builders
# --------------------------------------------------------------------------------------

_DEFAULT_CHUNK = 1024 * 1024  # 1 MiB

def _iter_file_range(path: Path, start: int, end: int, chunk_size: int = _DEFAULT_CHUNK) -> Iterator[bytes]:
    to_read = end - start + 1
    with path.open("rb") as f:
        f.seek(start)
        remaining = to_read
        while remaining > 0:
            chunk = f.read(min(chunk_size, remaining))
            if not chunk:
                break
            yield chunk
            remaining -= len(chunk)

def stream_file_response(
    request: Request,
    path: Path,
    *,
    commitment_hex: str,
    content_type: str = "application/octet-stream",
    filename: Optional[str] = None,
    chunk_size: int = _DEFAULT_CHUNK,
    cache_max_age: int = 86400,
) -> StreamingResponse:
    """
    Build a StreamingResponse for a file on disk, honoring a single Range.
    Sets: ETag, Accept-Ranges, Cache-Control, Content-Disposition (if filename provided).
    """
    if not path.exists() or not path.is_file():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Blob not found")

    size = path.stat().st_size

    # Conditional: If-None-Match
    etag = etag_for_commitment(commitment_hex)
    inm = request.headers.get("if-none-match")
    if inm is not None and inm.strip() == etag and size > 0:
        # Not modified
        resp = StreamingResponse(iter(()), status_code=status.HTTP_304_NOT_MODIFIED)
        resp.headers["ETag"] = etag
        resp.headers["Cache-Control"] = f"public, max-age={int(cache_max_age)}"
        return resp

    # Range
    range_header = request.headers.get("range")
    rspec = parse_range_header(range_header, size) if range_header else None

    if rspec is None:
        # Full content (200)
        body = _iter_file_range(path, 0, size - 1, chunk_size=chunk_size)
        resp = StreamingResponse(body, media_type=content_type, status_code=status.HTTP_200_OK)
        resp.headers["Content-Length"] = str(size)
    else:
        # Partial content (206)
        body = _iter_file_range(path, rspec.start, rspec.end, chunk_size=chunk_size)
        resp = StreamingResponse(body, media_type=content_type, status_code=status.HTTP_206_PARTIAL_CONTENT)
        resp.headers["Content-Range"] = rspec.to_content_range()
        resp.headers["Content-Length"] = str(rspec.size)

    # Common headers
    resp.headers["Accept-Ranges"] = "bytes"
    resp.headers["ETag"] = etag
    resp.headers["Cache-Control"] = f"public, max-age={int(cache_max_age)}"
    if filename:
        # RFC 6266
        safe = filename.replace("\"", "")
        resp.headers["Content-Disposition"] = f'inline; filename="{safe}"'
    return resp

def json_response(
    data: dict,
    *,
    commitment_hex: Optional[str] = None,
    status_code: int = status.HTTP_200_OK,
    cache_max_age: int = 120,
) -> JSONResponse:
    """
    JSON response with optional ETag derived from commitment and short cache headers.
    """
    headers = {}
    if commitment_hex:
        headers["ETag"] = etag_for_commitment(commitment_hex)
    headers["Cache-Control"] = f"public, max-age={int(cache_max_age)}"
    return JSONResponse(content=data, status_code=status_code, headers=headers)


# --------------------------------------------------------------------------------------
# Misc helpers
# --------------------------------------------------------------------------------------

def guess_content_type_for_blob(ns: Optional[int] = None, filename: Optional[str] = None) -> str:
    """
    Best-effort content type for blobs. We default to octet-stream; if a filename
    hint is available, use a simple extension-based mapping.
    """
    if filename:
        lower = filename.lower()
        if lower.endswith(".json"):
            return "application/json"
        if lower.endswith(".cbor"):
            return "application/cbor"
        if lower.endswith(".txt"):
            return "text/plain; charset=utf-8"
        if lower.endswith(".bin"):
            return "application/octet-stream"
    return "application/octet-stream"


# --------------------------------------------------------------------------------------
# Public exports
# --------------------------------------------------------------------------------------

__all__ = [
    "RangeSpec",
    "parse_range_header",
    "stream_file_response",
    "json_response",
    "normalize_commitment_hex",
    "etag_for_commitment",
    "guess_content_type_for_blob",
]
