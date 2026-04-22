"""Artifacts service: upload and retrieve content-addressed blobs."""

from __future__ import annotations

import base64
import binascii
import hashlib
import os
import time
from collections import defaultdict
from pathlib import Path
from typing import Dict, List, Tuple

from studio_services.errors import BadRequest, NotFound
from studio_services.models.artifacts import ArtifactKind, ArtifactMeta, ArtifactPut

# Lightweight in-process indexes (sufficient for local dev/tests).
_META_BY_ID: Dict[str, ArtifactMeta] = {}
_ADDRESS_INDEX: Dict[str, set[str]] = defaultdict(set)


def _storage_root() -> Path:
    base = os.getenv("STORAGE_DIR", "./.studio-services/storage")
    root = Path(base).expanduser().resolve() / "artifacts_compat"
    root.mkdir(parents=True, exist_ok=True)
    return root


def _path_for(artifact_id: str) -> Path:
    root = _storage_root()
    return root / artifact_id.removeprefix("0x")


def _compute_id(payload: bytes) -> str:
    return "0x" + hashlib.sha3_256(payload).hexdigest()


def _decode_content(req: ArtifactPut) -> bytes:
    if req.text is not None:
        return req.text.encode("utf-8")

    content = (req.content or "").strip()
    if not content:
        raise BadRequest("artifact content is required")

    enc = (req.encoding or "").strip().lower()

    if enc == "base64":
        try:
            return base64.b64decode(content, validate=True)
        except binascii.Error as e:
            raise BadRequest(f"invalid base64 artifact content: {e}") from e

    if enc == "hex":
        s = content[2:] if content.startswith("0x") else content
        try:
            return bytes.fromhex(s)
        except ValueError as e:
            raise BadRequest(f"invalid hex artifact content: {e}") from e

    if enc in ("", "utf8", "utf-8"):
        if content.startswith("0x"):
            try:
                return bytes.fromhex(content[2:])
            except Exception:
                pass

        if len(content) % 2 == 0:
            try:
                int(content, 16)
                return bytes.fromhex(content)
            except Exception:
                pass

        try:
            return base64.b64decode(content, validate=True)
        except binascii.Error:
            return content.encode("utf-8")

    raise BadRequest(f"unsupported encoding '{req.encoding}'")


def _normalize_mime(req: ArtifactPut) -> str:
    if req.media_type:
        return req.media_type
    defaults = {
        ArtifactKind.source: "text/x-python",
        ArtifactKind.manifest: "application/json",
        ArtifactKind.abi: "application/json",
        ArtifactKind.package: "application/zip",
        ArtifactKind.ir: "application/cbor",
        ArtifactKind.bytecode: "application/octet-stream",
        ArtifactKind.other: "application/octet-stream",
    }
    return defaults.get(req.kind, "application/octet-stream")


def put_artifact(req: ArtifactPut) -> ArtifactMeta:
    payload = _decode_content(req)
    artifact_id = _compute_id(payload)
    p = _path_for(artifact_id)

    if not p.exists():
        p.write_bytes(payload)

    meta = ArtifactMeta(
        id=artifact_id,
        kind=req.kind,
        media_type=_normalize_mime(req),
        size=len(payload),
        content_hash=artifact_id,
        filename=req.filename,
        chain_id=req.chain_id,
        address=req.address,
        code_hash=req.code_hash,
        labels=req.labels,
        created_at=int(time.time()),
        download_path=f"/artifacts/{artifact_id}",
    )
    _META_BY_ID[artifact_id] = meta

    if req.address:
        _ADDRESS_INDEX[req.address.lower()].add(artifact_id)

    return meta


def get_artifact(artifact_id: str) -> ArtifactMeta:
    meta = _META_BY_ID.get(artifact_id)
    p = _path_for(artifact_id)
    if meta is not None:
        return meta
    if not p.exists():
        raise NotFound("Artifact")

    # Reconstruct minimal metadata if only blob exists.
    return ArtifactMeta(
        id=artifact_id,
        kind=ArtifactKind.other,
        media_type="application/octet-stream",
        size=p.stat().st_size,
        content_hash=artifact_id,
        download_path=f"/artifacts/{artifact_id}",
    )


def get_artifact_bytes(artifact_id: str) -> Tuple[bytes, str]:
    p = _path_for(artifact_id)
    if not p.exists():
        raise NotFound("Artifact")
    meta = _META_BY_ID.get(artifact_id)
    mime = (meta.media_type if meta else None) or "application/octet-stream"
    return p.read_bytes(), mime


def list_artifacts_by_address(
    address: str, limit: int = 50, cursor: str | None = None
) -> List[ArtifactMeta]:
    _ = cursor
    ids = list(_ADDRESS_INDEX.get(address.lower(), set()))[:limit]
    out: List[ArtifactMeta] = []
    for aid in ids:
        try:
            out.append(get_artifact(aid))
        except Exception:
            continue
    return out


__all__ = [
    "put_artifact",
    "get_artifact",
    "get_artifact_bytes",
    "list_artifacts_by_address",
]
