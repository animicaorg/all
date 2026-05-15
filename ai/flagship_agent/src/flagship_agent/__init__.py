"""Animica flagship-agent training pipeline + offline inference fallback."""

from __future__ import annotations

__version__ = "0.1.0"

from flagship_agent.modes import EffectiveBackend, resolve_mode

__all__ = ["__version__", "EffectiveBackend", "resolve_mode"]
