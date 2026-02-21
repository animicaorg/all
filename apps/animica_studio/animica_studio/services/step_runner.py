from __future__ import annotations

from dataclasses import dataclass, asdict, field
from datetime import datetime, timezone
from typing import Any, Callable
import traceback
import uuid

from animica_studio.services.ena_store import EnaStore


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class StepState:
    name: str
    status: str = "pending"
    progress: int = 0
    logs: list[str] = field(default_factory=list)
    retry_action: str | None = None
    copy_command: str | None = None
    error: str | None = None


@dataclass
class StepRun:
    run_id: str
    flow: str
    status: str
    created_at: str
    updated_at: str
    steps: list[StepState]
    result: dict[str, Any] = field(default_factory=dict)


class StepRunner:
    """Resumable flow step runner persisted in EnaStore."""

    def __init__(self, store: EnaStore) -> None:
        self._store = store

    def create_or_resume(self, flow: str, step_names: list[str], run_id: str | None = None) -> StepRun:
        runs = self._store.get("step_runs", {})
        if run_id and run_id in runs:
            return self._deserialize(runs[run_id])
        new_id = run_id or f"{flow}-{uuid.uuid4().hex[:10]}"
        run = StepRun(
            run_id=new_id,
            flow=flow,
            status="running",
            created_at=_now(),
            updated_at=_now(),
            steps=[StepState(name=n) for n in step_names],
        )
        self._save(run)
        return run

    def run(self, flow: str, steps: list[tuple[str, Callable[[StepState], dict[str, Any]]]], run_id: str | None = None) -> StepRun:
        run = self.create_or_resume(flow, [s[0] for s in steps], run_id=run_id)
        by_name = {s.name: s for s in run.steps}
        for name, func in steps:
            step = by_name[name]
            if step.status == "completed":
                continue
            step.status = "running"
            step.logs.append(f"[{_now()}] started")
            self._save(run)
            try:
                payload = func(step) or {}
                step.progress = 100
                step.status = "completed"
                step.logs.append(f"[{_now()}] completed")
                if payload:
                    run.result[name] = payload
            except Exception as exc:  # noqa: BLE001
                step.status = "failed"
                step.error = str(exc)
                step.retry_action = f"Retry '{name}'"
                step.logs.append(traceback.format_exc(limit=2))
                run.status = "failed"
                run.updated_at = _now()
                self._save(run)
                return run
            run.updated_at = _now()
            self._save(run)
        run.status = "completed"
        run.updated_at = _now()
        self._save(run)
        return run

    def _save(self, run: StepRun) -> None:
        runs = dict(self._store.get("step_runs", {}))
        runs[run.run_id] = asdict(run)
        self._store.set("step_runs", runs)

    def _deserialize(self, obj: dict[str, Any]) -> StepRun:
        return StepRun(
            run_id=obj["run_id"],
            flow=obj["flow"],
            status=obj.get("status", "running"),
            created_at=obj.get("created_at", _now()),
            updated_at=obj.get("updated_at", _now()),
            steps=[StepState(**x) for x in obj.get("steps", [])],
            result=dict(obj.get("result", {})),
        )
