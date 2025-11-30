from __future__ import annotations

"""
Artifact models

- ArtifactPut: request body for storing an artifact blob (content-addressed).
  The server computes a deterministic `id` from the content and optional
  linkage hints (e.g., code_hash/address) and persists metadata.

- ArtifactMeta: metadata returned after storing or when fetching an artifact.
  Includes size, media type, linkage (address/chain/code hash), labels,
  and optional download path suitable for direct GETs from this service.

Notes
-----
* `content` is 0x-hex of the raw bytes.
* `kind` is a hint that influences defaults (e.g., media_type) and indexing.
* Linkage fields (address/chain_id/code_hash) are optional; they can be filled
  later by other endpoints (e.g., linking a verified address to an artifact).
"""

from enum import Enum
from typing import Dict, Optional

try:
    # Pydantic v2
    from pydantic import BaseModel, Field, PositiveInt, model_validator

    _IS_PYDANTIC_V2 = True
except Exception:  # pragma: no cover - v1 fallback
    from pydantic.v1 import BaseModel, Field, PositiveInt  # type: ignore

    _IS_PYDANTIC_V2 = False

from .common import Address, ChainId, Hash, Hex


class ArtifactKind(str, Enum):
    source = "source"  # e.g., Python contract source
    manifest = "manifest"  # contract manifest JSON
    abi = "abi"  # ABI JSON
    package = "package"  # bundled sources/assets (zip/tar)
    ir = "ir"  # intermediate representation bytes
    bytecode = "bytecode"  # compiled code bytes (if applicable)
    other = "other"  # anything else


_KIND_DEFAULT_MIMES: Dict[ArtifactKind, str] = {
    ArtifactKind.source: "text/x-python",
    ArtifactKind.manifest: "application/json",
    ArtifactKind.abi: "application/json",
    ArtifactKind.package: "application/zip",
    ArtifactKind.ir: "application/cbor",
    ArtifactKind.bytecode: "application/octet-stream",
    ArtifactKind.other: "application/octet-stream",
}


class ArtifactPut(BaseModel):
    """
    Store an artifact blob.

    Fields
    ------
    kind: ArtifactKind
        High-level kind of artifact (affects default media_type).
    content: Hex
        0x-prefixed hex of raw bytes to store (content-addressed).
    media_type: Optional[str]
        RFC 2046 media type; default is derived from `kind` if omitted.
    filename: Optional[str]
        Friendly file name for UX; not used for addressing.
    chain_id: Optional[ChainId]
        Optional chain linkage for discovery (e.g., manifests per network).
    address: Optional[Address]
        Optional contract address to associate with the artifact.
    code_hash: Optional[Hash]
        Optional compiled code hash to associate for discovery/verification.
    labels: Dict[str, str]
        Free-form small metadata (keys/values), e.g., {"template":"counter"}.
    """

    kind: ArtifactKind = Field(..., description="Artifact kind.")
    content: Hex = Field(..., description="0x-hex of the artifact bytes.")
    media_type: Optional[str] = Field(
        default=None, description="MIME type; defaults based on `kind` if omitted."
    )
    filename: Optional[str] = Field(
        default=None, description="Friendly filename (optional)."
    )
    chain_id: Optional[ChainId] = Field(
        default=None, description="Optional chain linkage."
    )
    address: Optional[Address] = Field(
        default=None, description="Optional associated contract address."
    )
    code_hash: Optional[Hash] = Field(
        default=None, description="Optional associated compiled code hash."
    )
    labels: Dict[str, str] = Field(
        default_factory=dict, description="Small key/value labels."
    )

    class Config:  # type: ignore[override]
        extra = "forbid"
        anystr_strip_whitespace = True

    if _IS_PYDANTIC_V2:

        @model_validator(mode="after")
        def _fill_defaults(cls, values: "ArtifactPut") -> "ArtifactPut":  # type: ignore[override]
            if values.media_type is None:
                values.media_type = _KIND_DEFAULT_MIMES.get(
                    values.kind, "application/octet-stream"
                )
            return values

    else:  # pragma: no cover - v1 compatibility

        @classmethod
        def validate(cls, value):  # type: ignore[override]
            obj = super().validate(value)
            if obj.media_type is None:
                obj.media_type = _KIND_DEFAULT_MIMES.get(
                    obj.kind, "application/octet-stream"
                )
            return obj


class ArtifactMeta(BaseModel):
    """
    Metadata of a stored artifact.
    """

    id: str = Field(..., description="Deterministic content-addressed id.")
    content_hash: Hash = Field(
        ..., description="Hash of raw bytes (e.g., sha3-512 as 0x-hex)."
    )
    size: PositiveInt = Field(..., description="Size in bytes.")
    kind: ArtifactKind = Field(..., description="Artifact kind.")
    media_type: str = Field(..., description="MIME media type.")
    filename: Optional[str] = Field(
        default=None, description="Friendly filename if provided."
    )
    chain_id: Optional[ChainId] = Field(
        default=None, description="Linked chain id, if any."
    )
    address: Optional[Address] = Field(
        default=None, description="Linked contract address, if any."
    )
    code_hash: Optional[Hash] = Field(
        default=None, description="Linked compiled code hash, if any."
    )
    labels: Dict[str, str] = Field(
        default_factory=dict, description="Free-form labels."
    )
    created_at: str = Field(..., description="ISO-8601 creation timestamp.")
    download_path: Optional[str] = Field(
        default=None, description="Relative path suitable for GET /artifacts/{id}."
    )

    class Config:  # type: ignore[override]
        extra = "forbid"


__all__ = ["ArtifactPut", "ArtifactMeta", "ArtifactKind"]
