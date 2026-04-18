"""
Animica Python toolbox.

This package hosts Python-side utilities for the Animica stack
(DA pipeline, VM helpers, CLI tools, etc).
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

def _resolve_repo_root() -> Path | None:
    override = os.environ.get("ANIMICA_REPO_ROOT")
    if override:
        candidate = Path(override).expanduser().resolve()
        if (candidate / "python" / "animica").exists():
            return candidate

    candidate = Path(__file__).resolve().parents[2]
    if (candidate / "python" / "animica").exists():
        return candidate
    return None


REPO_ROOT = _resolve_repo_root()
if REPO_ROOT is not None and str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

__all__ = ["bridge_banm", "cli", "config", "da", "mining", "stratum_pool", "wallet_cli"]
