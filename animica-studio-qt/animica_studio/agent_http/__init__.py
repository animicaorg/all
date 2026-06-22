"""Headless agent sidecar HTTP API package (FastAPI).

This package is intentionally Qt-free and DB-free so it can run inside the slim
``anm-studio-ide`` container image. See :mod:`animica_studio.agent_http.main`.
"""
from __future__ import annotations

__all__ = ["main"]
