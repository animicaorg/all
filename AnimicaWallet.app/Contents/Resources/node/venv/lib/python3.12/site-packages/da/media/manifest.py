"""
Media manifest data structures for DA layer.

Implements the canonical media_manifest.cddl schema as Python dataclasses
with CBOR serialization support.
"""

from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List, Optional, Union

try:
    import cbor2  # type: ignore
except ImportError:
    cbor2 = None  # type: ignore


class MediaKind(str, Enum):
    """Media type identifier."""
    IMAGE = "image"
    VIDEO = "video"
    AUDIO = "audio"
    DOCUMENT = "document"
    ARCHIVE = "archive"
    OTHER = "other"


@dataclass
class Integrity:
    """Integrity verification fields."""
    sha256: bytes  # 32-byte SHA-256 digest
    sha3_256: bytes  # 32-byte SHA3-256 digest
    nmt_root: Optional[bytes] = None  # Optional NMT root
    proof_type: Optional[str] = None  # Optional proof type

    def __post_init__(self):
        if len(self.sha256) != 32:
            raise ValueError("sha256 must be 32 bytes")
        if len(self.sha3_256) != 32:
            raise ValueError("sha3_256 must be 32 bytes")
        if self.nmt_root is not None and len(self.nmt_root) != 32:
            raise ValueError("nmt_root must be 32 bytes")


@dataclass
class ChunkingParams:
    """Chunking parameters for large media."""
    chunk_size: int  # Size per chunk in bytes
    num_chunks: int  # Total number of chunks
    chunk_commitments: List[bytes]  # Ordered list of chunk commitments

    def __post_init__(self):
        if self.num_chunks != len(self.chunk_commitments):
            raise ValueError("num_chunks must match length of chunk_commitments")
        for commit in self.chunk_commitments:
            if len(commit) != 32:
                raise ValueError("each chunk commitment must be 32 bytes")


@dataclass
class MediaManifest:
    """
    Media manifest for storing metadata about media in DA layer.
    
    Maps to the media_manifest.cddl schema.
    """
    # Required fields
    media_id: bytes  # 32-byte unique identifier
    kind: MediaKind  # Type of media
    content_type: str  # MIME type
    byte_size: int  # Total size in bytes
    integrity: Integrity  # Hash verification
    created_at: int  # Unix timestamp (seconds)
    uploader_address: bytes  # 20-byte address
    content: Union[bytes, ChunkingParams]  # Single commitment or chunking params
    
    # Optional metadata
    license: Optional[str] = None
    tags: List[str] = field(default_factory=list)
    thumbnail_commitment: Optional[bytes] = None
    original_commitment: Optional[bytes] = None
    width: Optional[int] = None
    height: Optional[int] = None
    duration: Optional[int] = None
    name: Optional[str] = None
    description: Optional[str] = None
    metadata: Dict[str, any] = field(default_factory=dict)

    def __post_init__(self):
        # Validate field sizes
        if len(self.media_id) != 32:
            raise ValueError("media_id must be 32 bytes")
        if len(self.uploader_address) != 20:
            raise ValueError("uploader_address must be 20 bytes")
        if isinstance(self.content, bytes) and len(self.content) != 32:
            raise ValueError("content commitment must be 32 bytes")
        if self.thumbnail_commitment is not None and len(self.thumbnail_commitment) != 32:
            raise ValueError("thumbnail_commitment must be 32 bytes")
        if self.original_commitment is not None and len(self.original_commitment) != 32:
            raise ValueError("original_commitment must be 32 bytes")
        
        # Validate content_type matches kind
        kind_prefixes = {
            MediaKind.IMAGE: "image/",
            MediaKind.VIDEO: "video/",
            MediaKind.AUDIO: "audio/",
        }
        if self.kind in kind_prefixes:
            if not self.content_type.startswith(kind_prefixes[self.kind]):
                raise ValueError(f"content_type must start with {kind_prefixes[self.kind]} for kind={self.kind}")

    def to_cbor(self) -> bytes:
        """Serialize to canonical CBOR."""
        if cbor2 is None:
            raise ImportError("cbor2 required for CBOR serialization")
        
        # Build dict matching CDDL schema integer keys
        data = {
            0: self.media_id,
            1: self.kind.value,
            2: self.content_type,
            3: self.byte_size,
            4: {
                0: self.integrity.sha256,
                1: self.integrity.sha3_256,
            },
            5: self.created_at,
            6: self.uploader_address,
        }
        
        # Add optional integrity fields
        if self.integrity.nmt_root is not None:
            data[4][2] = self.integrity.nmt_root
        if self.integrity.proof_type is not None:
            data[4][3] = self.integrity.proof_type
        
        # Handle content (single or chunked)
        if isinstance(self.content, bytes):
            data[7] = [self.content]  # Wrap in list as per CDDL
        else:
            data[7] = {
                0: self.content.chunk_size,
                1: self.content.num_chunks,
                2: self.content.chunk_commitments,
            }
        
        # Add optional fields
        if self.license:
            data[8] = self.license
        if self.tags:
            data[9] = self.tags
        if self.thumbnail_commitment:
            data[10] = self.thumbnail_commitment
        if self.original_commitment:
            data[11] = self.original_commitment
        if self.width is not None:
            data[12] = self.width
        if self.height is not None:
            data[13] = self.height
        if self.duration is not None:
            data[14] = self.duration
        if self.name:
            data[15] = self.name
        if self.description:
            data[16] = self.description
        if self.metadata:
            data[17] = self.metadata
        
        return cbor2.dumps(data, canonical=True)

    @classmethod
    def from_cbor(cls, data: bytes) -> MediaManifest:
        """Deserialize from CBOR."""
        if cbor2 is None:
            raise ImportError("cbor2 required for CBOR deserialization")
        
        obj = cbor2.loads(data)
        
        # Parse integrity
        integrity_data = obj[4]
        integrity = Integrity(
            sha256=integrity_data[0],
            sha3_256=integrity_data[1],
            nmt_root=integrity_data.get(2),
            proof_type=integrity_data.get(3),
        )
        
        # Parse content
        content_data = obj[7]
        if isinstance(content_data, list):
            # Single commitment wrapped in list
            content = content_data[0]
        else:
            # Chunking params
            content = ChunkingParams(
                chunk_size=content_data[0],
                num_chunks=content_data[1],
                chunk_commitments=content_data[2],
            )
        
        return cls(
            media_id=obj[0],
            kind=MediaKind(obj[1]),
            content_type=obj[2],
            byte_size=obj[3],
            integrity=integrity,
            created_at=obj[5],
            uploader_address=obj[6],
            content=content,
            license=obj.get(8),
            tags=obj.get(9, []),
            thumbnail_commitment=obj.get(10),
            original_commitment=obj.get(11),
            width=obj.get(12),
            height=obj.get(13),
            duration=obj.get(14),
            name=obj.get(15),
            description=obj.get(16),
            metadata=obj.get(17, {}),
        )


def compute_hashes(data: bytes) -> tuple[bytes, bytes]:
    """Compute both SHA-256 and SHA3-256 hashes."""
    sha256 = hashlib.sha256(data).digest()
    sha3_256 = hashlib.sha3_256(data).digest()
    return sha256, sha3_256


def create_manifest(
    content_commitment: Union[bytes, ChunkingParams],
    kind: MediaKind,
    content_type: str,
    byte_size: int,
    sha256: bytes,
    sha3_256: bytes,
    uploader_address: bytes,
    name: Optional[str] = None,
    tags: Optional[List[str]] = None,
    width: Optional[int] = None,
    height: Optional[int] = None,
    **kwargs
) -> MediaManifest:
    """
    Create a media manifest.
    
    Args:
        content_commitment: Single commitment or ChunkingParams
        kind: Type of media
        content_type: MIME type
        byte_size: Total size in bytes
        sha256: SHA-256 hash of content
        sha3_256: SHA3-256 hash of content
        uploader_address: 20-byte uploader address
        name: Optional display name
        tags: Optional tags for categorization
        width: Optional width (for images/video)
        height: Optional height (for images/video)
        **kwargs: Additional optional fields
    
    Returns:
        MediaManifest instance
    """
    integrity = Integrity(sha256=sha256, sha3_256=sha3_256)
    
    # Compute media_id as hash of core fields
    if isinstance(content_commitment, bytes):
        content_bytes = content_commitment
    else:
        # Hash all chunk commitments together
        content_bytes = b"".join(content_commitment.chunk_commitments)
    
    media_id_data = (
        kind.value.encode() +
        content_type.encode() +
        byte_size.to_bytes(8, "big") +
        sha256 +
        sha3_256 +
        content_bytes
    )
    media_id = hashlib.sha3_256(media_id_data).digest()
    
    return MediaManifest(
        media_id=media_id,
        kind=kind,
        content_type=content_type,
        byte_size=byte_size,
        integrity=integrity,
        created_at=int(time.time()),
        uploader_address=uploader_address,
        content=content_commitment,
        name=name,
        tags=tags or [],
        width=width,
        height=height,
        **kwargs
    )


def verify_manifest(manifest: MediaManifest, actual_data: bytes) -> bool:
    """
    Verify that manifest integrity fields match actual data.
    
    Args:
        manifest: The media manifest to verify
        actual_data: The actual media bytes
    
    Returns:
        True if hashes match, False otherwise
    """
    sha256, sha3_256 = compute_hashes(actual_data)
    
    if len(actual_data) != manifest.byte_size:
        return False
    if sha256 != manifest.integrity.sha256:
        return False
    if sha3_256 != manifest.integrity.sha3_256:
        return False
    
    return True
