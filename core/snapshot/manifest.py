from __future__ import annotations

import dataclasses
import json
from dataclasses import dataclass
from typing import Any, Iterable, Optional


@dataclass(slots=True)
class SnapshotChunk:
    name: str
    type: str
    size: int
    sha256: str
    index: Optional[int] = None
    url: Optional[str] = None

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "SnapshotChunk":
        return cls(
            name=str(data["name"]),
            type=str(data["type"]),
            size=int(data["size"]),
            sha256=str(data.get("sha256") or data.get("hash") or ""),
            index=data.get("index"),
            url=data.get("url"),
        )

    def to_dict(self) -> dict[str, Any]:
        payload = {
            "name": self.name,
            "type": self.type,
            "size": self.size,
            "sha256": self.sha256,
        }
        if self.index is not None:
            payload["index"] = self.index
        if self.url:
            payload["url"] = self.url
        return payload


@dataclass(slots=True)
class SnapshotManifest:
    schema_version: int
    chain_id: int
    network: Optional[str]
    created_at: str
    head_height: int
    head_hash: str
    total_size: int
    chunks: list[SnapshotChunk]
    db_engine: Optional[str] = None
    db_version: Optional[str] = None
    signature: Optional[dict[str, Any]] = None

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "SnapshotManifest":
        chunks_raw = data.get("chunks") or []
        chunks = [SnapshotChunk.from_dict(chunk) for chunk in chunks_raw]
        return cls(
            schema_version=int(
                data.get("schema_version")
                or data.get("version")
                or data.get("snapshot_version")
                or 1
            ),
            chain_id=int(data.get("chain_id") or 0),
            network=data.get("network"),
            created_at=str(data.get("created_at") or data.get("timestamp") or ""),
            head_height=int(data.get("head_height") or data.get("checkpoint_height") or 0),
            head_hash=str(data.get("head_hash") or data.get("checkpoint_hash") or ""),
            total_size=int(data.get("total_size") or 0),
            chunks=chunks,
            db_engine=data.get("db_engine"),
            db_version=data.get("db_version"),
            signature=data.get("signature"),
        )

    def to_dict(self) -> dict[str, Any]:
        payload = {
            "schema_version": self.schema_version,
            "chain_id": self.chain_id,
            "network": self.network,
            "created_at": self.created_at,
            "head_height": self.head_height,
            "head_hash": self.head_hash,
            "total_size": self.total_size,
            "chunks": [chunk.to_dict() for chunk in self.chunks],
            "db_engine": self.db_engine,
            "db_version": self.db_version,
        }
        if self.signature:
            payload["signature"] = self.signature
        return payload

    def canonical_payload(self) -> bytes:
        payload = self.to_dict()
        payload.pop("signature", None)
        return json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")


def iter_manifests(payload: dict[str, Any]) -> Iterable[SnapshotManifest]:
    snapshots = payload.get("snapshots")
    if isinstance(snapshots, list):
        for entry in snapshots:
            if isinstance(entry, dict):
                yield SnapshotManifest.from_dict(entry)
        return
    yield SnapshotManifest.from_dict(payload)


def select_best_manifest(manifests: Iterable[SnapshotManifest]) -> Optional[SnapshotManifest]:
    candidates = list(manifests)
    if not candidates:
        return None

    def sort_key(item: SnapshotManifest) -> tuple[int, str]:
        return (item.head_height, item.created_at or "")

    return max(candidates, key=sort_key)

