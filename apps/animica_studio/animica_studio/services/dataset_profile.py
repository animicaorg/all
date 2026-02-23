from __future__ import annotations

import hashlib
import json
from collections import Counter
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any


@dataclass
class DatasetProfile:
    bytes_total: int
    document_count: int
    avg_chars: float
    dedup_ratio: float
    language_mix: dict[str, float]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class DatasetProfiler:
    @staticmethod
    def profile(dataset_path: str) -> DatasetProfile:
        p = Path(dataset_path).expanduser()
        if not p.exists():
            return DatasetProfile(0, 0, 0.0, 1.0, {})
        shards: list[Path] = []
        if p.name == "manifest.json":
            payload = json.loads(p.read_text(encoding="utf-8"))
            for shard in payload.get("shards", []):
                shard_path = Path(str(shard.get("path", "")))
                if shard_path.exists():
                    shards.append(shard_path)
        elif p.is_file():
            shards = [p]
        hashes: set[str] = set()
        langs: Counter[str] = Counter()
        docs = 0
        total_chars = 0
        bytes_total = 0
        for shard in shards:
            bytes_total += shard.stat().st_size
            for line in shard.read_text(encoding="utf-8", errors="ignore").splitlines():
                line = line.strip()
                if not line:
                    continue
                text = line
                lang = "unknown"
                try:
                    row = json.loads(line)
                    if isinstance(row, dict):
                        text = str(row.get("text") or "")
                        lang = str(row.get("language") or "unknown")
                except Exception:
                    pass
                if not text:
                    continue
                docs += 1
                total_chars += len(text)
                langs[lang] += 1
                hashes.add(hashlib.sha1(text.encode("utf-8")).hexdigest())
        dedup = (len(hashes) / docs) if docs else 1.0
        mix = {k: round(v / docs, 4) for k, v in langs.items()} if docs else {}
        return DatasetProfile(bytes_total, docs, (total_chars / docs) if docs else 0.0, round(dedup, 4), mix)
