from __future__ import annotations

import hashlib
import json
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from animica_studio.services.dataset_manager import DatasetManager

APPROVED_SOURCE_CATEGORIES = {"wikipedia", "arxiv"}


@dataclass
class EvolutionQuotas:
    max_dataset_disk_gib: float = 20.0
    max_daily_download_gib: float = 2.0
    max_daily_training_hours: float = 4.0
    retain_last_versions: int = 3


class DatasetEvolutionEngine:
    STATES = {"DISABLED", "PLANNING", "BUILDING_DATASET", "TRAINING", "EVALUATING", "IDLE", "ERROR"}

    def __init__(self) -> None:
        self._dm = DatasetManager()
        self._registry_path = Path.home() / ".local" / "share" / "animica-studio" / "datasets" / "registry.json"
        self._registry_path.parent.mkdir(parents=True, exist_ok=True)
        self._state = "IDLE"
        self._init_registry()

    def state(self) -> str:
        return self._state

    def set_state(self, value: str) -> None:
        self._state = value if value in self.STATES else "ERROR"

    def load_registry(self) -> dict[str, Any]:
        try:
            return json.loads(self._registry_path.read_text(encoding="utf-8"))
        except Exception:
            return {"schema": "animica.dataset.registry.v1", "versions": [], "daily_usage": {}}

    def save_registry(self, payload: dict[str, Any]) -> None:
        self._registry_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    def preview_next_plan(
        self,
        run_report: dict[str, Any] | None,
        quotas: EvolutionQuotas,
        quality_level: str,
        approved_sources: list[str] | None = None,
    ) -> dict[str, Any]:
        self.set_state("PLANNING")
        sources = [s for s in (approved_sources or ["wikipedia", "arxiv"]) if s in APPROVED_SOURCE_CATEGORIES]
        report = run_report or {}
        loss = float(report.get("metrics", {}).get("loss") or 1.0)
        eval_metrics = report.get("metrics", {}).get("eval_metrics") or {}
        weakest = sorted(eval_metrics.items(), key=lambda kv: kv[1])[:2]
        weak_labels = [k for k, _ in weakest] or ["generalization"]

        target_docs = 200
        if quality_level in {"quality", "max_quality"}:
            target_docs = 500
        if loss > 1.0:
            target_docs += 200

        additions: list[dict[str, Any]] = []
        for label in weak_labels:
            additions.append(
                {
                    "topic": label.replace("eval_", ""),
                    "sources": sources,
                    "max_documents": target_docs,
                    "reason": f"Underperforming metric: {label}",
                }
            )

        est_disk_gib = round(min(quotas.max_dataset_disk_gib, 0.25 + (target_docs / 5000)), 3)
        plan = {
            "plan_id": hashlib.sha256(f"{time.time()}-{target_docs}-{weak_labels}".encode("utf-8")).hexdigest()[:16],
            "state": self.state(),
            "approved_sources": sources,
            "new_source_categories": [],
            "additions": additions,
            "estimated_disk_gib": est_disk_gib,
            "requires_approval": est_disk_gib > (quotas.max_dataset_disk_gib * 0.8),
            "quality_level": quality_level,
            "replay_buffer_weight": 0.3 if quality_level in {"fast", "balanced"} else 0.45,
            "hard_example_weight": 0.2 if quality_level in {"fast", "balanced"} else 0.3,
        }
        self.set_state("IDLE")
        return plan

    def apply_plan(self, plan: dict[str, Any], quotas: EvolutionQuotas, run_name: str) -> dict[str, Any]:
        self.set_state("BUILDING_DATASET")
        additions = plan.get("additions") or []
        if not additions:
            raise ValueError("Plan has no additions")
        topics = [str(item.get("topic") or "machine learning") for item in additions]
        max_docs = max(int(item.get("max_documents", 200)) for item in additions)
        max_bytes = int(min(quotas.max_dataset_disk_gib * (1024**3), 256 * (1024**2)))

        ds = self._dm.build_auto_dataset(
            name=f"{run_name}-evolved",
            max_documents=max_docs,
            max_bytes=max_bytes,
            topics=topics,
            languages=["en"],
            source_categories=list(plan.get("approved_sources") or ["wikipedia", "arxiv"]),
            synthetic_allowed=True,
        )
        dataset_id = ds.get("dataset_id") or Path(str(ds["dataset_dir"])).name

        registry = self.load_registry()
        versions = list(registry.get("versions") or [])
        versions.append(
            {
                "dataset_id": dataset_id,
                "manifest_path": ds["manifest_path"],
                "created_at": int(time.time()),
                "provenance": ds.get("manifest", {}).get("provenance", []),
                "stats": ds.get("stats", {}),
            }
        )
        keep = max(1, int(quotas.retain_last_versions))
        for old in versions[:-keep]:
            try:
                p = Path(str(old.get("manifest_path", ""))).parent
                if p.exists():
                    for child in p.glob("*"):
                        if child.is_file():
                            child.unlink(missing_ok=True)
                    p.rmdir()
            except Exception:
                continue
        registry["versions"] = versions[-keep:]
        self.save_registry(registry)
        self.set_state("IDLE")
        return ds

    def _init_registry(self) -> None:
        if self._registry_path.exists():
            return
        self.save_registry({"schema": "animica.dataset.registry.v1", "versions": [], "daily_usage": {}})
