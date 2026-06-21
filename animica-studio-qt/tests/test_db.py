"""Database CRUD round-trip tests (no network, no display)."""
from __future__ import annotations

from pathlib import Path

from animica_studio.models import (
    ApprovalDecision,
    AutonomyLevel,
    CommandApproval,
    FileIndexEntry,
    MessageRole,
    Project,
    Session,
    ToolCall,
    ToolStatus,
    new_id,
)


def _make_project(root: str) -> Project:
    return Project(id=new_id(), name="demo", root_path=root, project_type="python")


def test_project_crud(db, tmp_path: Path) -> None:
    project = db.upsert_project(_make_project(str(tmp_path)))
    # Round-trip by id and by path.
    fetched = db.get_project(project.id)
    assert fetched is not None
    assert fetched.id == project.id
    assert fetched.name == "demo"
    assert db.get_project_by_path(str(tmp_path)).id == project.id
    assert any(p.id == project.id for p in db.list_projects())

    # Upsert on the same root_path updates, does not duplicate.
    updated = Project(id=project.id, name="renamed", root_path=str(tmp_path))
    db.upsert_project(updated)
    assert db.get_project_by_path(str(tmp_path)).name == "renamed"
    assert len([p for p in db.list_projects() if p.root_path == str(tmp_path)]) == 1

    db.delete_project(project.id)
    assert db.get_project(project.id) is None


def test_session_and_message_crud(db, tmp_path: Path) -> None:
    project = db.upsert_project(_make_project(str(tmp_path)))
    session = db.create_session(project_id=project.id, title="Chat 1")
    assert session.id
    assert db.get_session(session.id) is not None
    assert len(db.list_sessions(project_id=project.id)) == 1

    db.update_session(session.id, title="Renamed")
    assert db.get_session(session.id).title == "Renamed"

    m1 = db.add_message(session_id=session.id, role=MessageRole.USER, content="hello")
    m2 = db.add_message(
        session_id=session.id, role=MessageRole.ASSISTANT, content="hi there"
    )
    msgs = db.list_messages(session.id)
    assert [m.id for m in msgs] == [m1.id, m2.id]  # created_at ascending
    assert msgs[0].role == MessageRole.USER
    assert msgs[1].content == "hi there"

    db.delete_messages(session.id)
    assert db.list_messages(session.id) == []

    db.delete_session(session.id)
    assert db.get_session(session.id) is None


def test_settings_kv(db) -> None:
    assert db.get_setting("missing") is None
    assert db.get_setting("missing", "fallback") == "fallback"
    db.set_setting("endpoint", "https://pool.animica.org/v1")
    assert db.get_setting("endpoint") == "https://pool.animica.org/v1"
    # Overwrite.
    db.set_setting("endpoint", "http://localhost:8000/v1")
    assert db.get_setting("endpoint") == "http://localhost:8000/v1"

    db.set_setting_json("models", ["anm-fast-8b", "anm-pro-70b"])
    assert db.get_setting_json("models") == ["anm-fast-8b", "anm-pro-70b"]
    assert db.get_setting_json("nope", {"a": 1}) == {"a": 1}


def test_tool_call_log(db, tmp_path: Path) -> None:
    project = db.upsert_project(_make_project(str(tmp_path)))
    session = db.create_session(project_id=project.id)
    tc = ToolCall(
        id=new_id(),
        session_id=session.id,
        name="read_file",
        arguments={"path": "main.py"},
        status=ToolStatus.RUNNING,
    )
    db.log_tool_call(tc)
    assert len(db.list_tool_calls(session.id)) == 1

    # Upsert by id updates status/output.
    tc.status = ToolStatus.OK
    tc.output = "file contents"
    db.log_tool_call(tc)
    calls = db.list_tool_calls(session.id)
    assert len(calls) == 1
    assert calls[0].status == ToolStatus.OK
    assert calls[0].output == "file contents"


def test_command_approvals(db, tmp_path: Path) -> None:
    project = db.upsert_project(_make_project(str(tmp_path)))
    assert db.find_always_allow("destructive", project.id) is False

    db.add_command_approval(
        CommandApproval(
            id=new_id(),
            project_id=project.id,
            command="rm -rf build",
            category="destructive",
            decision=ApprovalDecision.ALWAYS_ALLOW,
            always=True,
        )
    )
    assert db.find_always_allow("destructive", project.id) is True
    # A non-always (once) approval must not count as always-allow.
    assert db.find_always_allow("install", project.id) is False
    only_always = db.list_command_approvals(project_id=project.id, only_always=True)
    assert len(only_always) == 1


def test_file_index(db, tmp_path: Path) -> None:
    project = db.upsert_project(_make_project(str(tmp_path)))
    entry = FileIndexEntry(
        id=new_id(),
        project_id=project.id,
        path="src/app.py",
        language="python",
        summary="application entrypoint",
        symbols=["main", "run"],
        content_excerpt="def main(): ...",
    )
    db.upsert_file_index(entry)
    assert len(db.list_file_index(project.id)) == 1

    # Unique on (project_id, path): re-upsert updates rather than duplicates.
    entry.summary = "updated summary"
    db.upsert_file_index(entry)
    listed = db.list_file_index(project.id)
    assert len(listed) == 1
    assert listed[0].summary == "updated summary"

    # LIKE search across path/summary/symbols/excerpt.
    assert any(e.path == "src/app.py" for e in db.search_file_index(project.id, "app"))
    assert db.search_file_index(project.id, "updated")  # summary
    assert db.search_file_index(project.id, "main")  # symbols/excerpt

    db.clear_file_index(project.id)
    assert db.list_file_index(project.id) == []


def test_autonomy_level_roundtrip(db, tmp_path: Path) -> None:
    project = db.upsert_project(_make_project(str(tmp_path)))
    session = db.create_session(
        project_id=project.id, autonomy_level=AutonomyLevel.FULL_AGENT
    )
    fetched = db.get_session(session.id)
    assert fetched.autonomy_level == AutonomyLevel.FULL_AGENT
