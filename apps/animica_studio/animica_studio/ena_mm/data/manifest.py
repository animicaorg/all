from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
import json


@dataclass(slots=True)
class ProvenanceEntry:
    modality: str
    source: str
    license: str
    user_provided: bool = True


@dataclass(slots=True)
class MultimodalManifest:
    created_at: str = field(default_factory=lambda: datetime.now(tz=timezone.utc).isoformat())
    text_path: str = ""
    image_path: str = ""
    video_path: str = ""
    provenance: list[ProvenanceEntry] = field(default_factory=list)

    def to_json(self) -> dict:
        data = asdict(self)
        data["provenance"] = [asdict(p) for p in self.provenance]
        return data

    def save(self, path: str | Path) -> None:
        Path(path).write_text(json.dumps(self.to_json(), indent=2), encoding="utf-8")

    @classmethod
    def load(cls, path: str | Path) -> "MultimodalManifest":
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
        return cls(
            created_at=str(raw.get("created_at") or ""),
            text_path=str(raw.get("text_path") or ""),
            image_path=str(raw.get("image_path") or ""),
            video_path=str(raw.get("video_path") or ""),
            provenance=[ProvenanceEntry(**p) for p in raw.get("provenance", []) if isinstance(p, dict)],
        )
