from __future__ import annotations

from dataclasses import asdict, dataclass, field
from time import time
from typing import Any


@dataclass(slots=True)
class BootstrapRun:
    run_id: str
    started_at: float = field(default_factory=time)
    updated_at: float = field(default_factory=time)
    state: str = "IDLE"
    target_size: str = "big"
    source_providers: list[str] = field(default_factory=list)
    bytes_downloaded: int = 0
    bytes_total: int | None = None
    bytes_processed: int = 0
    shards_count: int = 0
    output_bytes: int = 0
    docs_processed: int = 0
    docs_total: int | None = None
    last_error: str = ""
    diagnostics: dict[str, Any] = field(default_factory=dict)
    output_dir: str = ""
    paused: bool = False
    log_lines: list[dict[str, str]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "BootstrapRun":
        data = dict(raw)
        data.setdefault("source_providers", [])
        data.setdefault("diagnostics", {})
        data.setdefault("log_lines", [])
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})
