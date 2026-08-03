"""In-memory TTL cache + persisted last-good snapshot.

The app caches the aggregate for ANIMICA_STATS_CACHE_TTL seconds (default 30)
and persists every successful aggregate to ANIMICA_STATS_SNAPSHOT_PATH
(default /var/lib/animica-stats/last_good.json). When the node RPC is down the
app serves the persisted snapshot with an ``X-Animica-Stale: true`` header.

Snapshot I/O is fully fault-tolerant: a read-only or missing filesystem never
breaks a request (writes are simply skipped, reads return None).
"""
from __future__ import annotations

import json
import os
import time
from typing import Any, Dict, Optional


class TTLCache:
    def __init__(self) -> None:
        self._store: Dict[str, tuple] = {}

    def get(self, key: str) -> Optional[Any]:
        entry = self._store.get(key)
        if entry is None:
            return None
        expires_at, value = entry
        if time.monotonic() >= expires_at:
            self._store.pop(key, None)
            return None
        return value

    def set(self, key: str, value: Any, ttl: float) -> None:
        self._store[key] = (time.monotonic() + max(0.0, float(ttl)), value)

    def clear(self) -> None:
        self._store.clear()


CACHE = TTLCache()


def default_ttl() -> float:
    try:
        return float(os.environ.get("ANIMICA_STATS_CACHE_TTL", "30") or "30")
    except ValueError:
        return 30.0


def snapshot_path() -> str:
    return (
        os.environ.get("ANIMICA_STATS_SNAPSHOT_PATH", "").strip()
        or "/var/lib/animica-stats/last_good.json"
    )


def save_snapshot(data: Dict[str, Any]) -> bool:
    """Persist the last-good aggregate. Never raises (read-only fs tolerated)."""
    path = snapshot_path()
    try:
        d = os.path.dirname(path)
        if d:
            os.makedirs(d, exist_ok=True)
        tmp = f"{path}.tmp.{os.getpid()}"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, separators=(",", ":"))
        os.replace(tmp, path)
        return True
    except (OSError, TypeError, ValueError):
        return False


def load_snapshot() -> Optional[Dict[str, Any]]:
    """Load the last-good aggregate; None if absent/corrupt. Never raises."""
    try:
        with open(snapshot_path(), "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else None
    except (OSError, json.JSONDecodeError, ValueError):
        return None
