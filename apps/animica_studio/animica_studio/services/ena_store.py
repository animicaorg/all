from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from threading import RLock
from typing import Any

from animica_studio.util.paths import app_data_dir


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class EnaStore:
    """JSON-backed persistent store for ENA guided automation state."""

    path: Path | None = None
    _lock: RLock = field(default_factory=RLock, init=False)

    def __post_init__(self) -> None:
        if self.path is None:
            self.path = app_data_dir() / "ena_store.json"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if not self.path.exists():
            self._write(
                {
                    "profiles": {
                        "active": "remote",
                        "remote": {"rpc_url": "https://mainnet.animica.org/rpc"},
                        "local": {"rpc_url": "http://127.0.0.1:8545/rpc"},
                    },
                    "jobs": [],
                    "artifacts": [],
                    "checkpoints": [],
                    "history": [],
                    "step_runs": {},
                    "debug_bundles": [],
                    "updated_at": _now(),
                }
            )

    def _read(self) -> dict[str, Any]:
        with self._lock:
            try:
                return json.loads(self.path.read_text(encoding="utf-8"))
            except Exception:
                return {}

    def _write(self, data: dict[str, Any]) -> None:
        with self._lock:
            data["updated_at"] = _now()
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps(data, indent=2, sort_keys=True), encoding="utf-8")
            tmp.replace(self.path)

    def get(self, key: str, default: Any = None) -> Any:
        return self._read().get(key, default)

    def set(self, key: str, value: Any) -> None:
        data = self._read()
        data[key] = value
        self._write(data)

    def append(self, key: str, item: dict[str, Any], *, dedupe_key: str | None = None) -> bool:
        data = self._read()
        arr = list(data.get(key, []))
        if dedupe_key and any(it.get(dedupe_key) == item.get(dedupe_key) for it in arr):
            return False
        arr.append(item)
        data[key] = arr
        self._write(data)
        return True
