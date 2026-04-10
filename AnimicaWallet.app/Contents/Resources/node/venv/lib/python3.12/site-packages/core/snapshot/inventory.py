from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Optional

from core.snapshot.paths import get_snapshots_dir, snapshot_path_display

INVENTORY_FILENAME = "inventory.json"


@dataclass(frozen=True)
class SnapshotEntry:
    chain_id: int
    checkpoint_height: int
    checkpoint_hash: str
    blocks_count: int
    accounts_count: int
    timestamp: int
    created_at: str
    total_size: int
    manifest_hash: str
    path: str
    path_display: str
    chunks: list[dict[str, Any]]

    def to_dict(self) -> dict[str, Any]:
        return {
            "chain_id": self.chain_id,
            "checkpoint_height": self.checkpoint_height,
            "checkpoint_hash": self.checkpoint_hash,
            "blocks_count": self.blocks_count,
            "accounts_count": self.accounts_count,
            "timestamp": self.timestamp,
            "created_at": self.created_at,
            "total_size": self.total_size,
            "manifest_hash": self.manifest_hash,
            "path": self.path,
            "path_display": self.path_display,
            "chunks": list(self.chunks),
        }


def _inventory_path(snapshots_dir: Path) -> Path:
    return snapshots_dir / INVENTORY_FILENAME


def _hash_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            h.update(chunk)
    return "0x" + h.hexdigest()


def _manifest_entry(
    snapshot_dir: Path, snapshots_dir: Optional[Path] = None
) -> Optional[SnapshotEntry]:
    manifest_path = snapshot_dir / "manifest.json"
    if not manifest_path.exists():
        return None
    try:
        manifest_data = json.loads(manifest_path.read_text(encoding="utf-8"))
    except Exception:
        return None
    chain_id = int(manifest_data.get("chain_id") or 0)
    checkpoint_height = int(
        manifest_data.get("checkpoint_height") or manifest_data.get("head_height") or 0
    )
    checkpoint_hash = str(
        manifest_data.get("checkpoint_hash") or manifest_data.get("head_hash") or ""
    )
    blocks_count = int(manifest_data.get("blocks_count") or 0)
    accounts_count = int(manifest_data.get("accounts_count") or 0)
    timestamp = int(manifest_data.get("timestamp") or 0)
    created_at = str(manifest_data.get("created_at") or "")
    chunks = list(manifest_data.get("chunks") or [])
    total_size = int(
        manifest_data.get("total_size")
        or sum(int(chunk.get("size") or 0) for chunk in chunks)
    )
    manifest_hash = _hash_file(manifest_path)
    data_dir = None
    if snapshots_dir is not None and snapshots_dir.name == "snapshots":
        data_dir = snapshots_dir.parent
    return SnapshotEntry(
        chain_id=chain_id,
        checkpoint_height=checkpoint_height,
        checkpoint_hash=checkpoint_hash,
        blocks_count=blocks_count,
        accounts_count=accounts_count,
        timestamp=timestamp,
        created_at=created_at,
        total_size=total_size,
        manifest_hash=manifest_hash,
        path=str(snapshot_dir),
        path_display=snapshot_path_display(snapshot_dir, data_dir=data_dir),
        chunks=chunks,
    )


def read_inventory(snapshots_dir: Optional[Path] = None) -> list[SnapshotEntry]:
    base_dir = snapshots_dir or get_snapshots_dir()
    inv_path = _inventory_path(base_dir)
    if not inv_path.exists():
        return []
    try:
        payload = json.loads(inv_path.read_text(encoding="utf-8"))
    except Exception:
        return []
    entries = []
    for item in payload.get("snapshots", []):
        try:
            entries.append(
                SnapshotEntry(
                    chain_id=int(item.get("chain_id") or 0),
                    checkpoint_height=int(item.get("checkpoint_height") or 0),
                    checkpoint_hash=str(item.get("checkpoint_hash") or ""),
                    blocks_count=int(item.get("blocks_count") or 0),
                    accounts_count=int(item.get("accounts_count") or 0),
                    timestamp=int(item.get("timestamp") or 0),
                    created_at=str(item.get("created_at") or ""),
                    total_size=int(item.get("total_size") or 0),
                    manifest_hash=str(item.get("manifest_hash") or ""),
                    path=str(item.get("path") or ""),
                    path_display=str(item.get("path_display") or item.get("path") or ""),
                    chunks=list(item.get("chunks") or []),
                )
            )
        except Exception:
            continue
    return entries


def write_inventory(
    snapshots: Iterable[SnapshotEntry], snapshots_dir: Optional[Path] = None
) -> None:
    base_dir = snapshots_dir or get_snapshots_dir()
    base_dir.mkdir(parents=True, exist_ok=True)
    inv_path = _inventory_path(base_dir)
    payload = {"snapshots": [entry.to_dict() for entry in snapshots]}
    inv_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def rebuild_inventory(snapshots_dir: Optional[Path] = None) -> list[SnapshotEntry]:
    base_dir = snapshots_dir or get_snapshots_dir()
    entries = []
    if base_dir.exists():
        for item in base_dir.iterdir():
            if not item.is_dir():
                continue
            entry = _manifest_entry(item, snapshots_dir=base_dir)
            if entry is None:
                continue
            entries.append(entry)
    entries.sort(key=lambda entry: (entry.chain_id, -entry.checkpoint_height))
    write_inventory(entries, base_dir)
    return entries


def list_snapshots_from_dirs(
    snapshots_dirs: Iterable[Path],
) -> list[SnapshotEntry]:
    entries: list[SnapshotEntry] = []
    seen: set[tuple[int, int, str, str]] = set()
    for snapshots_dir in snapshots_dirs:
        if not snapshots_dir.exists():
            continue
        try:
            dir_entries = rebuild_inventory(snapshots_dir)
        except Exception:
            dir_entries = []
        for entry in dir_entries:
            key = (
                entry.chain_id,
                entry.checkpoint_height,
                entry.checkpoint_hash,
                entry.path,
            )
            if key in seen:
                continue
            seen.add(key)
            entries.append(entry)
    entries.sort(key=lambda entry: (entry.chain_id, -entry.checkpoint_height))
    return entries


def upsert_snapshot(snapshot_dir: Path, snapshots_dir: Optional[Path] = None) -> None:
    entry = _manifest_entry(snapshot_dir, snapshots_dir=snapshots_dir)
    if entry is None:
        return
    base_dir = snapshots_dir or get_snapshots_dir()
    entries = read_inventory(base_dir)
    updated = []
    replaced = False
    for existing in entries:
        if (
            existing.chain_id == entry.chain_id
            and existing.checkpoint_height == entry.checkpoint_height
        ):
            updated.append(entry)
            replaced = True
        else:
            updated.append(existing)
    if not replaced:
        updated.append(entry)
    updated.sort(key=lambda e: (e.chain_id, -e.checkpoint_height))
    write_inventory(updated, base_dir)


def remove_snapshot(
    *,
    chain_id: int,
    checkpoint_height: int,
    snapshots_dir: Optional[Path] = None,
) -> None:
    base_dir = snapshots_dir or get_snapshots_dir()
    entries = read_inventory(base_dir)
    updated = [
        entry
        for entry in entries
        if not (
            entry.chain_id == chain_id
            and entry.checkpoint_height == checkpoint_height
        )
    ]
    write_inventory(updated, base_dir)


def latest_snapshot(
    chain_id: Optional[int] = None, snapshots_dir: Optional[Path] = None
) -> Optional[SnapshotEntry]:
    entries = read_inventory(snapshots_dir or get_snapshots_dir())
    if not entries:
        try:
            entries = rebuild_inventory(snapshots_dir)
        except PermissionError:
            return None
        except OSError:
            return None
    if chain_id is not None:
        entries = [entry for entry in entries if entry.chain_id == chain_id]
    if not entries:
        return None
    return max(entries, key=lambda entry: entry.checkpoint_height)


__all__ = [
    "SnapshotEntry",
    "read_inventory",
    "write_inventory",
    "rebuild_inventory",
    "list_snapshots_from_dirs",
    "upsert_snapshot",
    "remove_snapshot",
    "latest_snapshot",
    "INVENTORY_FILENAME",
]
