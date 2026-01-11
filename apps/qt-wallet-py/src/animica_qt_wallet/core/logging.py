from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path

from PySide6.QtCore import QStandardPaths


def _resolve_data_dir() -> Path:
    location = QStandardPaths.writableLocation(QStandardPaths.AppDataLocation)
    if location:
        return Path(location)
    return Path.home() / ".animica-qt-wallet"


def setup_logging() -> Path:
    data_dir = _resolve_data_dir()
    data_dir.mkdir(parents=True, exist_ok=True)
    log_path = data_dir / "animica-qt-wallet.log"

    logger = logging.getLogger()
    logger.setLevel(logging.INFO)

    formatter = logging.Formatter(
        fmt="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    file_handler = RotatingFileHandler(log_path, maxBytes=1_000_000, backupCount=3)
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)

    stream_handler = logging.StreamHandler()
    stream_handler.setFormatter(formatter)
    logger.addHandler(stream_handler)

    logger.info("Logging initialized at %s", log_path)
    return log_path
