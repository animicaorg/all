from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(slots=True)
class ModelEntry:
    name: str
    checkpoint_path: str
    created_at: float
    training_run_id: str | None = None
    quality_metrics: dict[str, Any] | None = None


class EnaModelRepository:
    def __init__(self, roots: list[str] | None = None) -> None:
        self._roots = [Path(r).expanduser() for r in (roots or ["./ena-training-runs", str(Path.home() / ".local/share/animica-studio/ena-training-runs")])]

    def list_models(self) -> list[ModelEntry]:
        out: list[ModelEntry] = []
        seen: set[str] = set()
        patterns = ["**/*.ckpt", "**/*.ckpt.json", "**/*.pt", "**/*.bin", "**/*.safetensors"]
        for root in self._roots:
            if not root.exists():
                continue
            for pat in patterns:
                for p in root.glob(pat):
                    if not p.is_file():
                        continue
                    rp = str(p.resolve())
                    if rp in seen:
                        continue
                    seen.add(rp)
                    report = p.parent / "run_report.json"
                    metrics: dict[str, Any] | None = None
                    if report.exists():
                        try:
                            import json

                            metrics = json.loads(report.read_text(encoding="utf-8"))
                        except Exception:
                            metrics = None
                    out.append(
                        ModelEntry(
                            name=p.stem,
                            checkpoint_path=rp,
                            created_at=p.stat().st_mtime,
                            training_run_id=p.parent.name,
                            quality_metrics=metrics,
                        )
                    )
        return sorted(out, key=lambda m: m.created_at, reverse=True)
