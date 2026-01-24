"""Path helpers for the Animica Miner GUI."""

from __future__ import annotations

import os
import platform
import sys
from pathlib import Path
from typing import Optional


def is_frozen() -> bool:
    """Return True if running in a frozen (PyInstaller) bundle."""
    return bool(getattr(sys, "frozen", False))


def get_resources_dir() -> Path:
    """Return the resolved resources directory for the app."""
    if is_frozen():
        meipass = getattr(sys, "_MEIPASS", None)
        if meipass:
            return Path(meipass)
    return Path(__file__).resolve().parents[1]


def get_config_dir() -> Path:
    """Return the default GUI config directory."""
    return Path.home() / ".animica" / "gui-miner"


def get_logs_dir() -> Path:
    """Return the GUI logs directory."""
    return get_config_dir() / "logs"


def get_startup_log_path() -> Path:
    """Return the startup log file path for today."""
    from datetime import datetime

    date_stamp = datetime.now().strftime("%Y%m%d")
    return get_logs_dir() / f"startup-{date_stamp}.log"


def get_app_support_dir() -> Path:
    """Return the platform-specific app support directory."""
    system = platform.system().lower()
    if system == "darwin":
        return Path.home() / "Library" / "Application Support" / "Animica"
    if system == "windows":
        return Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming")) / "Animica"
    return Path.home() / ".animica"


def get_node_data_dir() -> Path:
    """Return the directory for node data."""
    return get_app_support_dir() / "node-data"


def get_node_logs_dir() -> Path:
    """Return the directory for node logs."""
    return get_app_support_dir() / "node-logs"


def get_node_token_path() -> Path:
    """Return the RPC token file path."""
    return get_app_support_dir() / "rpc-token"


def get_node_payload_dir() -> Path:
    """Return the bundled node payload directory."""
    return get_resources_dir() / "node" / "animica-node"


def get_node_executable() -> Path:
    """Return the bundled node executable path."""
    return get_node_payload_dir() / "animica-node"


def get_node_manifest_path() -> Path:
    """Return the node manifest path inside Resources."""
    return get_resources_dir() / "node-manifest.json"


def get_gui_manifest_path() -> Path:
    """Return the GUI manifest path inside Resources."""
    return get_resources_dir() / "gui-manifest.json"


def resolve_app_resource(path: str | Path) -> Path:
    """Resolve a resource path inside the bundle."""
    return get_resources_dir() / Path(path)


def ensure_dirs(*paths: Path) -> None:
    """Ensure the provided directories exist."""
    for path in paths:
        path.mkdir(parents=True, exist_ok=True)


def get_last_crash_marker() -> Path:
    """Return the crash marker file path."""
    return get_logs_dir() / "last_crash.txt"


def get_last_crash_log() -> Optional[Path]:
    """Return the last crash log path if recorded."""
    marker = get_last_crash_marker()
    if not marker.exists():
        return None
    content = marker.read_text(encoding="utf-8").strip()
    return Path(content) if content else None
