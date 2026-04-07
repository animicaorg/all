from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from PySide6.QtWidgets import QApplication

from animica_studio.services.ena_contribution_engine import (
    CycleResult,
    EnaContributionConfig,
    EnaContributionEngine,
    _run_contribution_cycle_pure,
)


def test_run_contribution_cycle_pure_local_mode() -> None:
    cfg = {
        "enabled": True,
        "intensity": "low",
        "mode": "local",
        "services_url": "",
        "auto_start": False,
        "rpc_url": "",
        "worker_id": "",
    }
    out = _run_contribution_cycle_pure(cfg, {})
    assert out.ok is True
    assert out.status == "worked"
    assert out.metrics_delta == {"jobs_completed": 1, "submissions_ok": 1, "credits_earned": 0.0}
    assert out.metrics_set is not None and out.metrics_set["current_job_id"] == ""


def test_tick_uses_pure_worker_callable_and_applies_result(monkeypatch) -> None:
    app = QApplication.instance() or QApplication([])
    _ = app

    cfg = EnaContributionConfig(enabled=True, mode="local", intensity="low")
    engine = EnaContributionEngine(cfg)

    captured: dict[str, object] = {}

    class _Signal:
        def __init__(self) -> None:
            self._callbacks = []

        def connect(self, cb):
            self._callbacks.append(cb)

        def emit(self, *args):
            for cb in list(self._callbacks):
                cb(*args)

    class _FakeWorker:
        def __init__(self) -> None:
            self.result = _Signal()
            self.error = _Signal()
            self.finished = _Signal()

    class _FakeThread:
        def __init__(self, fn, *args) -> None:
            captured["fn"] = fn
            captured["args"] = args
            self.worker = _FakeWorker()

        def isRunning(self) -> bool:
            return False

        def start(self) -> None:
            self.worker.result.emit(CycleResult(ok=True, status="idle", metrics_set={"cpu_threads_in_use": 2}))
            self.worker.finished.emit()

    monkeypatch.setattr("animica_studio.services.ena_contribution_engine.WorkerThread", _FakeThread)

    engine._tick()

    assert captured["fn"] is _run_contribution_cycle_pure
    assert engine.metrics.cpu_threads_in_use == 2
    assert engine.state.value == "idle"
