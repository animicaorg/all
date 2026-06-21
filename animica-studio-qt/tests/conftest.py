"""Shared pytest fixtures for Animica Studio Qt.

Ensures the package root is importable, forces the offscreen Qt platform so UI
tests never need a display, and provides DB / project / QApplication fixtures.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

# Force headless Qt before any PySide6 import happens.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

# Make the project root importable when pytest is run from anywhere.
_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))


@pytest.fixture()
def db(tmp_path: Path):
    """An open, isolated :class:`Database` backed by a temp file."""
    from animica_studio.db import Database

    database = Database(tmp_path / "studio.db")
    try:
        yield database
    finally:
        database.close()


@pytest.fixture()
def project_dir(tmp_path: Path) -> Path:
    """A small on-disk project tree (with an ignored dir) for sandbox tests."""
    root = tmp_path / "proj"
    (root / "src").mkdir(parents=True)
    (root / "src" / "app.py").write_text("print('hello')\n", encoding="utf-8")
    (root / "README.md").write_text("# Demo\n", encoding="utf-8")
    (root / "pyproject.toml").write_text("[project]\nname='demo'\n", encoding="utf-8")
    # An ignored directory that must never appear in the file tree.
    (root / "__pycache__").mkdir()
    (root / "__pycache__" / "junk.pyc").write_text("x", encoding="utf-8")
    return root


@pytest.fixture(scope="session")
def qapp():
    """A single QApplication for UI tests (offscreen)."""
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])
    return app
