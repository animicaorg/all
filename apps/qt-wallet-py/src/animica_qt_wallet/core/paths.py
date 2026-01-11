from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QStandardPaths


def get_app_data_dir() -> Path:
    location = QStandardPaths.writableLocation(QStandardPaths.AppDataLocation)
    if location:
        return Path(location)
    return Path.home() / ".animica-qt-wallet"
