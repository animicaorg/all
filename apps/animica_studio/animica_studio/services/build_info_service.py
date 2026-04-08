"""Build and runtime metadata helpers for packaged and repo runs."""

from __future__ import annotations

import platform
import sys
from dataclasses import dataclass
from pathlib import Path

from animica_studio import __version__
from animica_studio.storage.config import discover_repo_root
from animica_studio.util.paths import app_data_dir, config_file, logs_dir


@dataclass(frozen=True)
class BuildInfo:
    app_version: str
    python_version: str
    platform_label: str
    packaged: bool
    executable: str
    repo_root: str
    app_data_dir: str
    config_path: str
    logs_dir: str


def collect_build_info() -> BuildInfo:
    repo_root = discover_repo_root()
    packaged = bool(getattr(sys, "frozen", False))
    return BuildInfo(
        app_version=__version__,
        python_version=sys.version.split()[0],
        platform_label=platform.platform(),
        packaged=packaged,
        executable=str(Path(sys.executable).resolve()),
        repo_root=str(repo_root) if repo_root else "",
        app_data_dir=str(app_data_dir()),
        config_path=str(config_file()),
        logs_dir=str(logs_dir()),
    )
