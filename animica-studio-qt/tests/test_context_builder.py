"""ContextBuilder size-budget tests (no network, no display)."""
from __future__ import annotations

from pathlib import Path

from animica_studio.agent.context import DEFAULT_MAX_CHARS, ContextBuilder
from animica_studio.models import Message, MessageRole
from animica_studio.services.project_service import ProjectService


def test_empty_builder_is_safe() -> None:
    builder = ContextBuilder()
    out = builder.build(user_request="anything")
    assert isinstance(out, str)


def test_budget_is_respected_with_large_inputs() -> None:
    builder = ContextBuilder(max_chars=2_000)
    huge = "x" * 50_000
    out = builder.build(
        user_request="do stuff",
        recent_tool_outputs=[huge, huge],
        recent_errors=[huge],
    )
    # Allow a small overhead margin for section headers / truncation markers.
    assert len(out) <= 2_000 + 500


def test_default_budget_caps_history() -> None:
    builder = ContextBuilder(max_chars=DEFAULT_MAX_CHARS)
    history = [
        Message(id=str(i), role=MessageRole.USER, content="y" * 5_000)
        for i in range(20)
    ]
    out = builder.build(user_request="summarize", history=history)
    assert len(out) <= DEFAULT_MAX_CHARS + 500


def test_secrets_are_redacted() -> None:
    builder = ContextBuilder(secrets=("supersecretkey",))
    out = builder.build(
        user_request="ctx",
        recent_tool_outputs=["token=supersecretkey trailing"],
    )
    assert "supersecretkey" not in out


def test_estimate_tokens_monotonic() -> None:
    builder = ContextBuilder()
    assert builder.estimate_tokens("") >= 1
    assert builder.estimate_tokens("a" * 400) >= builder.estimate_tokens("a" * 4)


def test_project_summary_included(project_dir: Path) -> None:
    svc = ProjectService()
    svc.open_project(str(project_dir))
    builder = ContextBuilder(project_service=svc, max_chars=DEFAULT_MAX_CHARS)
    out = builder.build(user_request="overview")
    # Project overview section should appear, and the budget still holds.
    assert "Project overview" in out or "File tree" in out
    assert len(out) <= DEFAULT_MAX_CHARS + 500
