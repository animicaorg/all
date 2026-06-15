"""Pytest configuration for the miner-gui test suite.

Ensures the in-tree ``animica_miner_gui`` package is importable even when it has
not been ``pip install``-ed (the sandbox runs the venv pytest against the source
tree). No Qt is required by these tests.
"""

from __future__ import annotations

import sys
from pathlib import Path

# Repo layout: apps/miner-gui/tests/conftest.py -> apps/miner-gui is the package
# root containing the importable ``animica_miner_gui`` package.
_PKG_ROOT = Path(__file__).resolve().parents[1]
if str(_PKG_ROOT) not in sys.path:
    sys.path.insert(0, str(_PKG_ROOT))
