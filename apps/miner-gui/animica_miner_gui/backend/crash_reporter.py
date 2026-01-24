"""Crash reporting utilities for the Animica Miner GUI."""

from __future__ import annotations

import logging
import sys
import threading
import traceback
from datetime import datetime
from pathlib import Path
from typing import Optional

from animica_miner_gui.backend.app_paths import ensure_dirs, get_last_crash_marker, get_logs_dir

logger = logging.getLogger(__name__)


def _write_crash_report(exc: BaseException) -> Path:
    logs_dir = get_logs_dir()
    ensure_dirs(logs_dir)
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    crash_path = logs_dir / f"crash-{timestamp}.log"
    crash_path.write_text("".join(traceback.format_exception(exc)), encoding="utf-8")
    get_last_crash_marker().write_text(str(crash_path), encoding="utf-8")
    return crash_path


def install_exception_hooks() -> None:
    """Install global exception hooks for main and threads."""

    def handle_exception(exc_type, exc_value, exc_traceback) -> None:
        if exc_value is None:
            return
        try:
            crash_path = _write_crash_report(exc_value)
            logger.error("Unhandled exception captured. Crash report: %s", crash_path)
        except Exception as hook_exc:
            logger.error("Failed to write crash report: %s", hook_exc)
        sys.__excepthook__(exc_type, exc_value, exc_traceback)

    def handle_thread_exception(args: threading.ExceptHookArgs) -> None:
        try:
            crash_path = _write_crash_report(args.exc_value)
            logger.error("Unhandled thread exception captured. Crash report: %s", crash_path)
        except Exception as hook_exc:
            logger.error("Failed to write thread crash report: %s", hook_exc)

    sys.excepthook = handle_exception
    threading.excepthook = handle_thread_exception


def clear_crash_marker() -> None:
    """Clear stored crash marker."""
    marker = get_last_crash_marker()
    if marker.exists():
        marker.unlink()


def load_last_crash() -> Optional[Path]:
    """Return the last crash report path, if any."""
    marker = get_last_crash_marker()
    if not marker.exists():
        return None
    content = marker.read_text(encoding="utf-8").strip()
    return Path(content) if content else None
