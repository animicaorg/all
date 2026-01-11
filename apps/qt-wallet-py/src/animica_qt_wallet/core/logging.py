from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path

from animica_qt_wallet.core.paths import get_app_data_dir


def setup_logging() -> Path:
    data_dir = get_app_data_dir()
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
