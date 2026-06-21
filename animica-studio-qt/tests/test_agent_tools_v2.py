"""Tests for the upgraded agent tools: native execution, scaffold, read_files,
safe patching wiring, and incremental reindex."""
from __future__ import annotations

from pathlib import Path

import pytest

from animica_studio.agent.executor import ToolExecutor
from animica_studio.agent.tools import ToolContext, ToolManager
from animica_studio.models import AutonomyLevel
from animica_studio.services.project_service import ProjectService
from animica_studio.services.templates_service import TemplatesService


@pytest.fixture()
def project(db, tmp_path: Path):
    root = tmp_path / "proj"
    root.mkdir()
    (root / "main.py").write_text("x = 1\n", encoding="utf-8")
    ps = ProjectService(db)
    ps.open_project(str(root))
    return ps


def _manager(ps, **extra) -> ToolManager:
    return ToolManager(ToolContext(project_service=ps, **extra))


def test_parse_native_tool_calls_with_json_string_args():
    parsed = ToolExecutor.parse_native_tool_calls(
        [{"id": "c1", "name": "read_file", "arguments": "{\"path\": \"main.py\"}"}]
    )
    assert len(parsed) == 1
    assert parsed[0].name == "read_file"
    assert parsed[0].arguments == {"path": "main.py"}
    assert parsed[0].id == "c1"


def test_executor_native_path_preserves_tool_call_id(project):
    mgr = _manager(project)
    ex = ToolExecutor(mgr, autonomy=AutonomyLevel.FULL_AGENT)
    res = ex.execute("", native_tool_calls=[{"id": "abc", "name": "read_file", "arguments": {"path": "main.py"}}])
    assert res.any_executed
    assert res.tool_calls[0].id == "abc"
    # tool_messages echo the SAME id so OpenAI tool/result pairing is valid.
    assert res.tool_messages[0]["tool_call_id"] == "abc"
    assert "x = 1" in res.tool_messages[0]["content"]


def test_read_files_batch(project):
    (project.root / "b.py").write_text("y = 2\n", encoding="utf-8")
    mgr = _manager(project)
    r = mgr.call("read_files", {"paths": ["main.py", "b.py", "missing.py"]})
    assert r.ok
    assert "x = 1" in r.output and "y = 2" in r.output
    assert "could not read" in r.output  # missing file reported, batch survives


def test_patch_file_uses_safe_applier_and_reports_errors(project):
    mgr = _manager(project)
    # A search that does not exist must fail loudly, not silently corrupt.
    r = mgr.call("patch_file", {"path": "main.py", "search": "NOPE", "replace": "z"})
    assert not r.ok
    assert "not found" in (r.error or "").lower()
    # The file is unchanged.
    assert project.read_file("main.py") == "x = 1\n"


def test_scaffold_project_lays_down_template(project):
    ts = TemplatesService()
    mgr = _manager(project, templates_service=ts)
    r = mgr.call("scaffold_project", {"template_id": "python_cli", "name": "Demo App", "path": "cli"})
    assert r.ok, r.error
    assert project.exists("cli/main.py")
    # Substituted, runnable Python (no leftover unresolved f-string token).
    content = project.read_file("cli/main.py")
    assert "{{name}}" not in content
    compile(content, "main.py", "exec")  # must be syntactically valid


def test_scaffold_unknown_template(project):
    mgr = _manager(project, templates_service=TemplatesService())
    r = mgr.call("scaffold_project", {"template_id": "does_not_exist"})
    assert not r.ok


def test_run_tests_autodetects_command(project, monkeypatch):
    calls = {}

    class FakeRunner:
        def is_risky(self, cmd):
            return False

        def classify(self, cmd):
            return None

        def run_blocking(self, cmd, timeout=None):
            calls["cmd"] = cmd
            return (0, "ok")

    mgr = _manager(project, command_runner=FakeRunner())
    r = mgr.call("run_tests", {})
    assert r.ok
    assert r.data.get("passed") is True
    # compileall is the python fallback when there are no tests.
    assert "compileall" in calls["cmd"] or "pytest" in calls["cmd"]
