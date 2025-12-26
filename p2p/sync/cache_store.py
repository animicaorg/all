from __future__ import annotations

import contextlib
import json
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Optional

from core.utils.hash import sha3_256

log = logging.getLogger("animica.p2p.sync.cache")


@dataclass(slots=True)
class SyncCacheConfig:
    base_dir: Path
    max_block_bytes: int = 256 * 1024 * 1024
    max_block_entries: int = 2000
    max_header_entries: int = 5000
    state_flush_interval_sec: float = 5.0
    prune_interval_sec: float = 60.0


@dataclass(slots=True)
class SyncCacheState:
    headers: list[dict[str, Any]] = field(default_factory=list)
    best_header_hash: Optional[str] = None
    block_queue: list[str] = field(default_factory=list)
    block_queue_heights: dict[str, int] = field(default_factory=dict)
    peer_penalties: dict[str, int] = field(default_factory=dict)
    last_validated_height: int = 0
    target_height: Optional[int] = None
    paused: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "headers": list(self.headers),
            "best_header_hash": self.best_header_hash,
            "block_queue": list(self.block_queue),
            "block_queue_heights": dict(self.block_queue_heights),
            "peer_penalties": dict(self.peer_penalties),
            "last_validated_height": self.last_validated_height,
            "target_height": self.target_height,
            "paused": self.paused,
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "SyncCacheState":
        return cls(
            headers=list(payload.get("headers") or []),
            best_header_hash=payload.get("best_header_hash"),
            block_queue=list(payload.get("block_queue") or []),
            block_queue_heights=dict(payload.get("block_queue_heights") or {}),
            peer_penalties=dict(payload.get("peer_penalties") or {}),
            last_validated_height=int(payload.get("last_validated_height") or 0),
            target_height=payload.get("target_height"),
            paused=bool(payload.get("paused", False)),
        )


class SyncCacheStore:
    def __init__(self, config: SyncCacheConfig) -> None:
        self.config = config
        self.base_dir = Path(config.base_dir).expanduser()
        self.blocks_dir = self.base_dir / "blocks"
        self.state_path = self.base_dir / "state.json"
        self.index_path = self.base_dir / "blocks" / "index.json"
        self.base_dir.mkdir(parents=True, exist_ok=True)
        self.blocks_dir.mkdir(parents=True, exist_ok=True)
        self._index = self._load_index()

    def _load_index(self) -> dict[str, Any]:
        if not self.index_path.exists():
            return {"entries": {}, "total_bytes": 0}
        try:
            payload = json.loads(self.index_path.read_text(encoding="utf-8"))
        except Exception:
            log.warning("Failed to load sync cache index; rebuilding")
            return {"entries": {}, "total_bytes": 0}
        if not isinstance(payload, dict):
            return {"entries": {}, "total_bytes": 0}
        payload.setdefault("entries", {})
        payload.setdefault("total_bytes", 0)
        return payload

    def _write_json(self, path: Path, payload: dict[str, Any]) -> None:
        tmp_path = path.with_suffix(".tmp")
        tmp_path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
        tmp_path.replace(path)

    def load_state(self) -> SyncCacheState:
        if not self.state_path.exists():
            return SyncCacheState()
        try:
            payload = json.loads(self.state_path.read_text(encoding="utf-8"))
        except Exception:
            log.warning("Failed to load sync cache state; starting fresh")
            return SyncCacheState()
        if not isinstance(payload, dict):
            return SyncCacheState()
        return SyncCacheState.from_dict(payload)

    def save_state(self, state: SyncCacheState) -> None:
        payload = state.to_dict()
        payload["updated_at"] = time.time()
        payload["version"] = 1
        self._write_json(self.state_path, payload)

    def _block_path(self, block_hash: bytes) -> Path:
        return self.blocks_dir / f"{block_hash.hex()}.bin"

    def put_block(
        self,
        block_hash: bytes,
        raw_bytes: bytes,
        *,
        height: Optional[int] = None,
        source_peer: Optional[str] = None,
    ) -> None:
        if not block_hash:
            return
        path = self._block_path(block_hash)
        checksum = sha3_256(raw_bytes).hex()
        path.write_bytes(raw_bytes)
        entry = {
            "size": len(raw_bytes),
            "checksum": checksum,
            "height": height,
            "source_peer": source_peer,
            "last_access": time.time(),
            "last_verified": time.time(),
        }
        entries: dict[str, Any] = self._index.get("entries", {})
        previous = entries.get(block_hash.hex())
        if previous:
            self._index["total_bytes"] = max(
                0, int(self._index.get("total_bytes", 0)) - int(previous.get("size") or 0)
            )
        entries[block_hash.hex()] = entry
        self._index["entries"] = entries
        self._index["total_bytes"] = int(self._index.get("total_bytes", 0)) + len(raw_bytes)
        self._write_json(self.index_path, self._index)
        self.prune()

    def get_block(self, block_hash: bytes) -> Optional[bytes]:
        if not block_hash:
            return None
        entries: dict[str, Any] = self._index.get("entries", {})
        entry = entries.get(block_hash.hex())
        if not entry:
            return None
        path = self._block_path(block_hash)
        if not path.exists():
            self.invalidate_block(block_hash)
            return None
        raw_bytes = path.read_bytes()
        if len(raw_bytes) != int(entry.get("size") or 0):
            self.invalidate_block(block_hash)
            return None
        checksum = sha3_256(raw_bytes).hex()
        if checksum != entry.get("checksum"):
            self.invalidate_block(block_hash)
            return None
        entry["last_access"] = time.time()
        entry["last_verified"] = entry.get("last_verified") or time.time()
        entries[block_hash.hex()] = entry
        self._index["entries"] = entries
        self._write_json(self.index_path, self._index)
        return raw_bytes

    def invalidate_block(self, block_hash: bytes) -> None:
        if not block_hash:
            return
        entries: dict[str, Any] = self._index.get("entries", {})
        entry = entries.pop(block_hash.hex(), None)
        if entry:
            self._index["total_bytes"] = max(
                0, int(self._index.get("total_bytes", 0)) - int(entry.get("size") or 0)
            )
        self._index["entries"] = entries
        path = self._block_path(block_hash)
        if path.exists():
            path.unlink()
        self._write_json(self.index_path, self._index)

    def invalidate_from_height(self, height: int) -> int:
        entries: dict[str, Any] = self._index.get("entries", {})
        to_remove = []
        for key, meta in entries.items():
            entry_height = meta.get("height")
            if entry_height is not None and int(entry_height) > height:
                to_remove.append(key)
        removed = 0
        for key in to_remove:
            block_hash = bytes.fromhex(key)
            self.invalidate_block(block_hash)
            removed += 1
        return removed

    def clear(self) -> None:
        entries = list(self._index.get("entries", {}).keys())
        for key in entries:
            with contextlib.suppress(ValueError):
                block_hash = bytes.fromhex(key)
                self.invalidate_block(block_hash)
        self._index = {"entries": {}, "total_bytes": 0}
        if self.state_path.exists():
            self.state_path.unlink()

    def prune(self) -> None:
        entries: dict[str, Any] = self._index.get("entries", {})
        if not entries:
            return
        total_bytes = int(self._index.get("total_bytes", 0))
        max_bytes = int(self.config.max_block_bytes)
        max_entries = int(self.config.max_block_entries)
        if total_bytes <= max_bytes and len(entries) <= max_entries:
            return
        ordered = sorted(
            entries.items(),
            key=lambda item: float(item[1].get("last_access") or 0.0),
        )
        for key, meta in ordered:
            if total_bytes <= max_bytes and len(entries) <= max_entries:
                break
            block_hash = bytes.fromhex(key)
            total_bytes = max(0, total_bytes - int(meta.get("size") or 0))
            entries.pop(key, None)
            path = self._block_path(block_hash)
            if path.exists():
                path.unlink()
        self._index["entries"] = entries
        self._index["total_bytes"] = total_bytes
        self._write_json(self.index_path, self._index)

    def cache_size_bytes(self) -> int:
        return int(self._index.get("total_bytes", 0))

    def cache_entries(self) -> int:
        return len(self._index.get("entries", {}))
