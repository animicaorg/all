from __future__ import annotations

"""
Animica • DA • Wire Messages
===========================

Dataclasses for the *binary/on-the-wire* DA protocol. These are the canonical
message shapes that :mod:`da.protocol.encoding` serializes (CBOR/msgspec) and
authenticates (optional frame checksum).

Message Families
----------------
- INV*:    Announcements (e.g., new blob commitments available).
- GET*:    Requests for data or proofs.
- REPLY*:  Responses that carry data/proofs (names kept short as "Proof" / "BlobChunk").
- ERR:     Error response with a numeric code.

Conventions
-----------
- `commitment` is a 32-byte NMT root (bytes).
- `namespace` is uint32 (int).
- `chain_id` is the CAIP-2 numeric chain id (int).
- Ranged blob transfers use (offset, data, total_size). Final chunk is indicated by
  `eof=True`. Servers may choose chunk size; clients must reassemble in-order by offset.

Backwards-compatibility notes
-----------------------------
Adding new fields to an existing message class must be done with care and behind a
protocol-version gate in :mod:`da.protocol.encoding`. Adding new message types requires
a new `MsgType` value and registry entry.

"""

from dataclasses import dataclass, field
from enum import IntEnum
from typing import ClassVar, Dict, List, Optional, Tuple, Type, Union


# =============================================================================
# Type IDs
# =============================================================================

class MsgType(IntEnum):
    # Announcements
    INV_COMMITMENT = 0x01        # New blob commitment exists (namespace-scoped)
    INV_SHARES     = 0x02        # Optional: share/range availability notice

    # Requests
    GET_BLOB       = 0x10        # Request blob (optionally a byte-range)
    GET_PROOF      = 0x11        # Request DAS proof for sample indices

    # Responses
    PROOF          = 0x20        # DAS proof response (indices + branches)
    BLOB_CHUNK     = 0x21        # Blob bytes chunk (streamed/ranged)

    # Error
    ERROR          = 0xFF        # Error response


# =============================================================================
# Helpers
# =============================================================================

def _expect_len(name: str, b: bytes, n: int) -> None:
    if not isinstance(b, (bytes, bytearray)) or len(b) != n:
        raise ValueError(f"{name} must be {n} bytes")

def _expect_u32(name: str, v: int) -> None:
    if not isinstance(v, int) or v < 0 or v > 0xFFFFFFFF:
        raise ValueError(f"{name} must be uint32")

def _expect_nonneg(name: str, v: int) -> None:
    if not isinstance(v, int) or v < 0:
        raise ValueError(f"{name} must be a non-negative int")


# =============================================================================
# Messages
# =============================================================================

@dataclass(slots=True, frozen=True)
class InvCommitment:
    """Announcement that a blob with the given commitment is available."""
    type_id: ClassVar[int] = int(MsgType.INV_COMMITMENT)

    chain_id: int
    namespace: int
    commitment: bytes           # 32 bytes (NMT root)
    size: int                   # original blob size in bytes

    def __post_init__(self) -> None:
        _expect_len("commitment", self.commitment, 32)
        _expect_u32("namespace", self.namespace)
        _expect_nonneg("size", self.size)


@dataclass(slots=True, frozen=True)
class InvShares:
    """
    Optional notice advertising availability of share ranges for a namespace.
    Mostly useful for specialized sampling relays; clients may ignore.
    """
    type_id: ClassVar[int] = int(MsgType.INV_SHARES)

    chain_id: int
    namespace: int
    ranges: List[Tuple[int, int]]  # [(start_share, count), ...]

    def __post_init__(self) -> None:
        _expect_u32("namespace", self.namespace)
        for (start, count) in self.ranges:
            _expect_nonneg("start_share", start)
            _expect_nonneg("count", count)


@dataclass(slots=True, frozen=True)
class GetBlob:
    """Request the raw blob bytes (optionally a byte-range)."""
    type_id: ClassVar[int] = int(MsgType.GET_BLOB)

    chain_id: int
    commitment: bytes               # 32 bytes
    range_start: Optional[int] = None
    range_len: Optional[int] = None

    def __post_init__(self) -> None:
        _expect_len("commitment", self.commitment, 32)
        if (self.range_start is None) ^ (self.range_len is None):
            raise ValueError("range_start and range_len must be both set or both None")
        if self.range_start is not None:
            _expect_nonneg("range_start", self.range_start)  # type: ignore[arg-type]
            _expect_nonneg("range_len", self.range_len)      # type: ignore[arg-type]


@dataclass(slots=True, frozen=True)
class GetProof:
    """Request DAS proof branches for the given sample indices."""
    type_id: ClassVar[int] = int(MsgType.GET_PROOF)

    chain_id: int
    commitment: bytes               # 32 bytes
    indices: List[int]              # sample leaf indices

    def __post_init__(self) -> None:
        _expect_len("commitment", self.commitment, 32)
        for i in self.indices:
            _expect_nonneg("index", i)


@dataclass(slots=True, frozen=True)
class Proof:
    """DAS proof response with indices and corresponding proof branches."""
    type_id: ClassVar[int] = int(MsgType.PROOF)

    chain_id: int
    commitment: bytes               # 32 bytes
    indices: List[int]
    branches: List[bytes]           # per-index proof branch bytes (opaque to the wire layer)
    root: Optional[bytes] = None    # optional NMT root echo (32 bytes) for convenience

    def __post_init__(self) -> None:
        _expect_len("commitment", self.commitment, 32)
        if self.root is not None:
            _expect_len("root", self.root, 32)
        for i in self.indices:
            _expect_nonneg("index", i)
        # branches are opaque; ensure bytes-like
        for b in self.branches:
            if not isinstance(b, (bytes, bytearray)):
                raise ValueError("branches must be bytes-like")


@dataclass(slots=True, frozen=True)
class BlobChunk:
    """
    Chunked/ranged blob response. Servers may send multiple chunks; clients
    should reassemble by (offset, data) until eof=True. `total_size` is the
    full blob size (not just the range).
    """
    type_id: ClassVar[int] = int(MsgType.BLOB_CHUNK)

    chain_id: int
    commitment: bytes               # 32 bytes
    offset: int                     # byte offset within the blob
    data: bytes                     # chunk payload
    total_size: int                 # total blob size in bytes
    eof: bool = False               # True for the final chunk of this transfer

    def __post_init__(self) -> None:
        _expect_len("commitment", self.commitment, 32)
        _expect_nonneg("offset", self.offset)
        _expect_nonneg("total_size", self.total_size)
        if not isinstance(self.data, (bytes, bytearray)):
            raise ValueError("data must be bytes-like")
        if self.offset + len(self.data) > self.total_size:
            # out-of-bounds or inconsistent metadata
            raise ValueError("chunk exceeds declared total_size")


@dataclass(slots=True, frozen=True)
class Error:
    """Error response carrying a numeric code and message."""
    type_id: ClassVar[int] = int(MsgType.ERROR)

    code: int                       # implementation-specific but stable per release
    message: str
    relates_to: Optional[int] = None  # optional original type_id

    def __post_init__(self) -> None:
        # Codes are small non-negative ints; keep space for future namespacing.
        _expect_nonneg("code", self.code)
        if self.relates_to is not None and self.relates_to < 0:
            raise ValueError("relates_to must be non-negative when provided")


# =============================================================================
# Registry (type_id -> class)
# =============================================================================

ProtocolMessage = Union[InvCommitment, InvShares, GetBlob, GetProof, Proof, BlobChunk, Error]

MESSAGE_REGISTRY: Dict[int, Type[ProtocolMessage]] = {
    InvCommitment.type_id: InvCommitment,
    InvShares.type_id:     InvShares,
    GetBlob.type_id:       GetBlob,
    GetProof.type_id:      GetProof,
    Proof.type_id:         Proof,
    BlobChunk.type_id:     BlobChunk,
    Error.type_id:         Error,
}

__all__ = [
    "MsgType",
    "InvCommitment",
    "InvShares",
    "GetBlob",
    "GetProof",
    "Proof",
    "BlobChunk",
    "Error",
    "ProtocolMessage",
    "MESSAGE_REGISTRY",
]
