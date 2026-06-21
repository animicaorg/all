"""Agent package for Animica Studio Qt (tool manager + orchestration engine).

Public API:
    - :class:`~animica_studio.agent.engine.AgentEngine` — the orchestrator the UI
      binds to (also re-exported from ``animica_studio.agent.agent``).
    - :class:`~animica_studio.agent.tools.ToolManager` / ``ToolContext`` — the tool
      registry and its collaborator bundle.
    - :class:`~animica_studio.agent.planner.Planner` — structured task planning.
    - :class:`~animica_studio.agent.executor.ToolExecutor` — tool-call execution.
    - :class:`~animica_studio.agent.context.ContextBuilder` — model context assembly.

``AgentEngine`` is imported lazily to keep this package importable even before the
services/Qt layers are available.
"""
from __future__ import annotations

from .context import ContextBuilder
from .executor import ExecutionResult, ToolExecutor
from .planner import Planner, PlanResult
from .tools import ToolContext, ToolManager

__all__ = [
    "ContextBuilder",
    "ExecutionResult",
    "ToolExecutor",
    "Planner",
    "PlanResult",
    "ToolContext",
    "ToolManager",
    "AgentEngine",
]


def __getattr__(name: str):  # PEP 562 lazy attribute (engine pulls in PySide6)
    if name == "AgentEngine":
        from .engine import AgentEngine

        return AgentEngine
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
