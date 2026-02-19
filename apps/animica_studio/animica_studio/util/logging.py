"""Logging setup: rotating file handler + console handler."""

from __future__ import annotations

import logging
import logging.handlers
from pathlib import Path

_MAX_BYTES = 2 * 1024 * 1024  # 2 MB per file
_BACKUP_COUNT = 5


def setup_logging(log_dir: Path, app_version: str = "unknown") -> None:
    """Configure the root logger with a rotating file handler and console handler.

    Parameters
    ----------
    log_dir:
        Directory where ``animica_studio.log`` will be written.
    app_version:
        Application version string included in every log record.
    """
    fmt = f"%(asctime)s [%(levelname)-8s] [v{app_version}] %(name)s: %(message)s"
    datefmt = "%Y-%m-%dT%H:%M:%S"
    formatter = logging.Formatter(fmt, datefmt=datefmt)

    root = logging.getLogger()
    root.setLevel(logging.DEBUG)

    # Rotating file handler
    log_file = log_dir / "animica_studio.log"
    file_handler = logging.handlers.RotatingFileHandler(
        log_file,
        maxBytes=_MAX_BYTES,
        backupCount=_BACKUP_COUNT,
        encoding="utf-8",
    )
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(formatter)

    # Console handler
    console_handler = logging.StreamHandler()
    console_handler.setLevel(logging.INFO)
    console_handler.setFormatter(formatter)

    root.addHandler(file_handler)
    root.addHandler(console_handler)

    logging.getLogger(__name__).info(
        "Logging initialised — log file: %s", log_file
    )
