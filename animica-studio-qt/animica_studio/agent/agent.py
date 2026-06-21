"""Convenience facade for the agent layer.

The contract (CONTRACTS §4/§8) places :class:`AgentEngine` at
``animica_studio.agent.engine``. This module re-exports the orchestrator and its
principal collaborators under ``animica_studio.agent.agent`` for ergonomic imports
(e.g. ``from animica_studio.agent.agent import AgentEngine``). It adds no behavior.
"""
from __future__ import annotations

from .context import ContextBuilder
from .engine import MAX_FIX_ATTEMPTS, MAX_TURN_ITERATIONS, AgentEngine
from .executor import ExecutionResult, ToolExecutor
from .planner import Planner, PlanResult
from .tools import ToolContext, ToolManager

__all__ = [
    "AgentEngine",
    "ToolManager",
    "ToolContext",
    "ToolExecutor",
    "ExecutionResult",
    "Planner",
    "PlanResult",
    "ContextBuilder",
    "MAX_TURN_ITERATIONS",
    "MAX_FIX_ATTEMPTS",
]
