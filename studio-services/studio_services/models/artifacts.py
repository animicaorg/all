from __future__ import annotations

"""Artifact models with compatibility for base64/hex/text payload uploads."""

from enum import Enum
from typing import Any, Dict, Optional

try:
    from pydantic import BaseModel, Field, model_validator
except Exception:  # pragma: no cover
    from pydantic.v1 import BaseModel, Field, root_validator  # type: ignore

from .common import Address, ChainId, Hash


class ArtifactKind(str, Enum):
    source = "source"
    manifest = "manifest"
    abi = "abi"
    package = "package"
    ir = "ir"
    bytecode = "bytecode"
    other = "other"


class ArtifactPut(BaseModel):
    kind: ArtifactKind = Field(default=ArtifactKind.other)
    media_type: Optional[str] = Field(
        default=None,
        alias="mediaType",
        description="MIME type hint.",
    )
    encoding: Optional[str] = Field(
        default=None,
        description="Encoding for content: base64|hex|utf8 (optional).",
    )
    content: Optional[str] = Field(
        default=None,
        description="Encoded content string (base64 or hex).",
    )
    text: Optional[str] = Field(
        default=None,
        description="UTF-8 content shortcut.",
    )

    filename: Optional[str] = Field(default=None)
    chain_id: Optional[ChainId] = Field(default=None, alias="chainId")
    address: Optional[Address] = Field(default=None)
    code_hash: Optional[Hash] = Field(default=None, alias="codeHash")
    labels: Dict[str, str] = Field(default_factory=dict)

    if "model_validator" in globals():

        @model_validator(mode="before")
        @classmethod
        def _coerce_aliases(cls, data: Any) -> Any:  # type: ignore[override]
            if not isinstance(data, dict):
                return data
            out = dict(data)
            if "media_type" not in out and "mediaType" in out:
                out["media_type"] = out["mediaType"]
            if "chain_id" not in out and "chainId" in out:
                out["chain_id"] = out["chainId"]
            if "code_hash" not in out and "codeHash" in out:
                out["code_hash"] = out["codeHash"]
            return out

        @model_validator(mode="after")
        def _validate_payload(self) -> "ArtifactPut":  # type: ignore[override]
            if not self.content and self.text is None:
                raise ValueError("Either content or text is required")
            return self

    else:  # pragma: no cover

        @root_validator(pre=True)
        def _coerce_aliases_v1(cls, values: Dict[str, Any]) -> Dict[str, Any]:  # type: ignore[override]
            out = dict(values or {})
            if "media_type" not in out and "mediaType" in out:
                out["media_type"] = out["mediaType"]
            if "chain_id" not in out and "chainId" in out:
                out["chain_id"] = out["chainId"]
            if "code_hash" not in out and "codeHash" in out:
                out["code_hash"] = out["codeHash"]
            if not out.get("content") and out.get("text") is None:
                raise ValueError("Either content or text is required")
            return out

    class Config:  # type: ignore[override]
        populate_by_name = True
        extra = "ignore"


class ArtifactMeta(BaseModel):
    id: str = Field(..., description="Artifact id.")
    kind: ArtifactKind = Field(default=ArtifactKind.other)
    media_type: str = Field(default="application/octet-stream", alias="mediaType")
    size: int = Field(default=0)
    content_hash: Optional[str] = Field(default=None, alias="contentHash")
    filename: Optional[str] = Field(default=None)
    chain_id: Optional[ChainId] = Field(default=None, alias="chainId")
    address: Optional[Address] = Field(default=None)
    code_hash: Optional[Hash] = Field(default=None, alias="codeHash")
    labels: Dict[str, str] = Field(default_factory=dict)
    created_at: Optional[int] = Field(default=None, alias="createdAt")
    download_path: Optional[str] = Field(default=None, alias="downloadPath")

    class Config:  # type: ignore[override]
        populate_by_name = True
        extra = "ignore"


__all__ = ["ArtifactPut", "ArtifactMeta", "ArtifactKind"]
