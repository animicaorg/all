from __future__ import annotations

from pathlib import Path

import pytest

from animica_studio.services import job_runner
from animica_studio.services.job_runner import ResolvedCli, run_cli_blocking


def test_run_cli_prefixes_resolved_animica(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict[str, object] = {}

    def fake_resolve(_cfg=None):
        return ResolvedCli(argv_prefix=["/abs/path/animica"], env={"A": "1"})

    def fake_run(argv, **kwargs):
        seen["argv"] = argv
        seen["kwargs"] = kwargs

        class Result:
            returncode = 0
            stdout = "ok"
            stderr = ""
            args = argv

        return Result()

    monkeypatch.setattr(job_runner, "resolve_animica_cli", fake_resolve)
    monkeypatch.setattr(job_runner.subprocess, "run", fake_run)

    result = run_cli_blocking(["node", "status"], timeout_s=5)

    assert result.returncode == 0
    assert seen["argv"] == ["/abs/path/animica", "node", "status"]


def test_run_cli_rejects_prefixed_animica() -> None:
    with pytest.raises(ValueError):
        run_cli_blocking(["animica", "node", "status"])


def test_static_guard_no_direct_spawn_outside_runner() -> None:
    root = Path(__file__).resolve().parents[1] / "animica_studio"
    runner_path = root / "services" / "job_runner.py"

    allowed = {
        "services/cli_runner.py",
        "services/process_manager.py",
        "services/ena_service.py",
        "services/ena_daemon.py",
        "services/ena_tools.py",
        "ui/widgets/ena_panel.py",
        "ui/pages/ide_page.py",
    }
    offenders: list[str] = []
    for py in root.rglob("*.py"):
        rel = str(py.relative_to(root))
        if py == runner_path or rel in allowed:
            continue
        text = py.read_text(encoding="utf-8")
        if "subprocess." in text or "QProcess(" in text:
            offenders.append(str(py.relative_to(root)))

    assert offenders == [], f"Direct process spawning found outside job_runner: {offenders}"
