"""Per-OS application-data directory helpers."""

from __future__ import annotations

import os
import sys
from pathlib import Path

_APP_NAME_LINUX = "animica-studio"
_APP_NAME_MAC = "Animica Studio"
_APP_NAME_WIN = "Animica Studio"


def app_data_dir() -> Path:
    """Return the per-OS application-data directory and ensure it exists.

    * Linux  : ``~/.local/share/animica-studio``
    * macOS  : ``~/Library/Application Support/Animica Studio``
    * Windows: ``%APPDATA%\\Animica Studio``
    """
    if sys.platform == "win32":
        base = Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming"))
        path = base / _APP_NAME_WIN
    elif sys.platform == "darwin":
        path = Path.home() / "Library" / "Application Support" / _APP_NAME_MAC
    else:
        xdg = os.environ.get("XDG_DATA_HOME", "")
        base = Path(xdg) if xdg else Path.home() / ".local" / "share"
        path = base / _APP_NAME_LINUX

    path.mkdir(parents=True, exist_ok=True)
    return path


def logs_dir() -> Path:
    """Return the log directory (inside the app-data dir) and ensure it exists."""
    d = app_data_dir() / "logs"
    d.mkdir(parents=True, exist_ok=True)
    return d


def config_file() -> Path:
    """Return the full path to the JSON config file."""
    return app_data_dir() / "config.json"
