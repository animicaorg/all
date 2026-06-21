"""Tests for the relevance-ranked file search and the engine build/verify loop."""
from __future__ import annotations

from pathlib import Path

import pytest

from animica_studio.agent.engine import AgentEngine
from animica_studio.config import Settings
from animica_studio.models import (
    AutonomyLevel,
    FileIndexEntry,
    Project,
    new_id,
)
from animica_studio.services.animica_client import ChatResult
from animica_studio.services.project_service import ProjectService


# --------------------------------------------------------------------------- #
# File-index search ranking (Cluster C regression)
# --------------------------------------------------------------------------- #
def _idx(project_id, path, summary="", symbols=None, excerpt="", mtime=0.0):
    return FileIndexEntry(
        id=new_id(),
        project_id=project_id,
        path=path,
        language="python",
        summary=summary,
        symbols=symbols or [],
        content_excerpt=excerpt,
        mtime=mtime,
    )


def test_search_tokenizes_natural_language_query(db, tmp_path):
    proj = db.upsert_project(Project(id=new_id(), name="p", root_path=str(tmp_path)))
    db.upsert_file_index(_idx(proj.id, "auth/login.py", summary="user login handler", symbols=["login"]))
    db.upsert_file_index(_idx(proj.id, "models/user.py", summary="user model", symbols=["User"]))
    db.upsert_file_index(_idx(proj.id, "utils/format.py", summary="formatting helpers"))

    # The OLD code matched the whole phrase as one LIKE and returned nothing.
    hits = db.search_file_index(proj.id, "add a user login endpoint", limit=10)
    paths = [h.path for h in hits]
    assert "auth/login.py" in paths
    assert "models/user.py" in paths
    # Filename/symbol match ('login') should outrank the summary-only 'user' match.
    assert paths[0] == "auth/login.py"
    # Unrelated file should rank last or be excluded.
    assert "utils/format.py" not in paths or paths.index("utils/format.py") > paths.index("auth/login.py")


def test_search_empty_query_returns_recent(db, tmp_path):
    proj = db.upsert_project(Project(id=new_id(), name="p", root_path=str(tmp_path)))
    db.upsert_file_index(_idx(proj.id, "old.py", mtime=1.0))
    db.upsert_file_index(_idx(proj.id, "new.py", mtime=99.0))
    hits = db.search_file_index(proj.id, "the a of", limit=10)  # all stopwords
    assert hits[0].path == "new.py"  # most recent first


def test_delete_file_index(db, tmp_path):
    proj = db.upsert_project(Project(id=new_id(), name="p", root_path=str(tmp_path)))
    db.upsert_file_index(_idx(proj.id, "gone.py", symbols=["gone"]))
    assert db.search_file_index(proj.id, "gone", limit=5)
    db.delete_file_index(proj.id, "gone.py")
    assert not db.search_file_index(proj.id, "gone", limit=5)


# --------------------------------------------------------------------------- #
# Engine build → verify → fix loop (Clusters A + B)
# --------------------------------------------------------------------------- #
class FakeClient:
    """Returns pre-scripted ChatResults from chat_full()."""

    def __init__(self, scripted):
        self._scripted = list(scripted)
        self.calls = 0

    def chat_full(self, messages, **kw):
        self.calls += 1
        if self._scripted:
            return self._scripted.pop(0)
        return ChatResult(text="(no more)")

    # planner path (unused here) / ping
    def chat(self, messages, **kw):
        return ""


class FakeRunner:
    """Scripted verify command runner."""

    def __init__(self, results):
        self._results = list(results)
        self.commands = []

    def is_risky(self, cmd):
        return False

    def classify(self, cmd):
        return None

    def set_cwd(self, cwd):
        pass

    def run_blocking(self, cmd, timeout=None):
        self.commands.append(cmd)
        return self._results.pop(0) if self._results else (0, "ok")


@pytest.fixture()
def opened_project(db, tmp_path):
    root = tmp_path / "app"
    root.mkdir()
    (root / "main.py").write_text("def main():\n    return 0\n", encoding="utf-8")
    ps = ProjectService(db)
    ps.open_project(str(root))
    return ps, root


def _write_call(path, content):
    import json

    return ChatResult(
        text="",
        tool_calls=[{"id": new_id(), "name": "write_file", "arguments": json.dumps({"path": path, "content": content})}],
    )


def test_engine_build_verify_fix_loop(qapp, db, opened_project):
    ps, root = opened_project
    settings = Settings(autonomy_level=AutonomyLevel.FULL_AGENT, streaming=False)

    # Script: write a file → say done (verify FAILS) → write again → done (verify PASSES).
    client = FakeClient([
        _write_call("feature.py", "def f(:\n"),       # 1: broken edit
        ChatResult(text="Implemented the feature."),    # 2: 'done' -> verify fails
        _write_call("feature.py", "def f():\n    return 1\n"),  # 3: fix
        ChatResult(text="Fixed and verified."),         # 4: 'done' -> verify passes
    ])
    runner = FakeRunner([(1, "SyntaxError: invalid syntax"), (0, "")])

    engine = AgentEngine(
        settings, db,
        project_service=ps,
        client=client,
        command_runner=runner,
        indexer=None,
        git_service=None,
    )
    session = db.create_session(project_id=None)
    engine.set_session(session.id)

    finals: list[str] = []
    engine.assistantMessage.connect(finals.append)

    engine._run_turn("change the feature implementation")

    # The file the agent wrote exists with the fixed content.
    assert ps.read_file("feature.py") == "def f():\n    return 1\n"
    # The engine auto-verified twice (fail then pass) rather than trusting the model.
    assert len(runner.commands) == 2
    # Final user-facing message is clean prose from the last turn.
    assert finals and finals[-1] == "Fixed and verified."


def test_engine_native_tool_call_executes(qapp, db, opened_project):
    ps, root = opened_project
    settings = Settings(autonomy_level=AutonomyLevel.FULL_AGENT, streaming=False)
    client = FakeClient([
        _write_call("hello.py", "print('hi')\n"),
        ChatResult(text="Created hello.py."),
    ])
    runner = FakeRunner([(0, "")])
    engine = AgentEngine(settings, db, project_service=ps, client=client, command_runner=runner, indexer=None)
    engine.set_session(db.create_session().id)
    finals: list[str] = []
    engine.assistantMessage.connect(finals.append)

    engine._run_turn("make a hello script")

    assert ps.exists("hello.py")
    assert finals[-1] == "Created hello.py."


def test_engine_strips_tool_call_json_from_final(qapp, db, opened_project):
    ps, _ = opened_project
    settings = Settings(autonomy_level=AutonomyLevel.SUGGEST, streaming=False)
    # Model answers with prose + a leftover fenced tool_call in the SAME message.
    leaky = "All set!\n\n```tool_call\n{\"tool\": \"read_file\", \"arguments\": {}}\n```"
    client = FakeClient([ChatResult(text=leaky), ChatResult(text="done")])
    engine = AgentEngine(settings, db, project_service=ps, client=client, command_runner=FakeRunner([]), indexer=None)
    engine.set_session(db.create_session().id)
    finals: list[str] = []
    engine.assistantMessage.connect(finals.append)
    engine._run_turn("hi")
    # Whatever the final is, it must not contain raw protocol JSON.
    assert all("tool_call" not in f for f in finals)
