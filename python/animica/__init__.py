"""
Animica Python toolbox.

This package hosts Python-side utilities for the Animica stack
(DA pipeline, VM helpers, CLI tools, etc).
"""

from __future__ import annotations

import sys
from pathlib import Path

# Editable installs expose `/root/animica/python` but not the monorepo root.
# Add the repo root so sibling packages like `aicf` and `capabilities` resolve
# when the CLI is launched via the console-script entrypoint.
REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

__all__ = ["cli", "config", "da", "mining", "stratum_pool", "wallet_cli"]
