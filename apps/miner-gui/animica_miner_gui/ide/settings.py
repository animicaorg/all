"""IDE settings persistence utilities."""

from __future__ import annotations

import json
import logging
import platform
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List

from PySide6.QtCore import QStandardPaths

logger = logging.getLogger(__name__)


@dataclass
class IDESettings:
    """Settings persisted for the IDE experience."""

    recent_projects: List[str] = field(default_factory=list)
    last_workspace: str = ""
    open_files: List[str] = field(default_factory=list)
    active_file: str = ""
    autosave_enabled: bool = True
    autosave_interval_ms: int = 5000
    explorer_url: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "recent_projects": self.recent_projects,
            "last_workspace": self.last_workspace,
            "open_files": self.open_files,
            "active_file": self.active_file,
            "autosave_enabled": self.autosave_enabled,
            "autosave_interval_ms": self.autosave_interval_ms,
            "explorer_url": self.explorer_url,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "IDESettings":
        return cls(
            recent_projects=list(data.get("recent_projects", [])),
            last_workspace=data.get("last_workspace", ""),
            open_files=list(data.get("open_files", [])),
            active_file=data.get("active_file", ""),
            autosave_enabled=bool(data.get("autosave_enabled", True)),
            autosave_interval_ms=int(data.get("autosave_interval_ms", 5000)),
            explorer_url=str(data.get("explorer_url", "")),
        )


def ide_settings_path() -> Path:
    """Return the platform-appropriate path for ide.json."""
    system = platform.system()
    if system == "Darwin":
        base_dir = Path.home() / "Library" / "Application Support" / "Animica" / "gui-miner"
    else:
        app_data = QStandardPaths.writableLocation(QStandardPaths.AppDataLocation)
        if not app_data:
            base_dir = Path.home() / ".config" / "Animica" / "gui-miner"
        else:
            base_dir = Path(app_data) / "gui-miner"
    return base_dir / "ide.json"


def load_ide_settings() -> IDESettings:
    """Load IDE settings from disk."""
    path = ide_settings_path()
    if not path.exists():
        return IDESettings()

    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return IDESettings.from_dict(data)
    except (json.JSONDecodeError, OSError) as exc:
        logger.warning("Failed to load IDE settings: %s", exc)
        return IDESettings()


def save_ide_settings(settings: IDESettings) -> None:
    """Persist IDE settings to disk."""
    path = ide_settings_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(settings.to_dict(), indent=2), encoding="utf-8")
    except OSError as exc:
        logger.warning("Failed to save IDE settings: %s", exc)
