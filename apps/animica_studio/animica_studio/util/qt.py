"""Qt lifetime helpers for safe QObject/QThread access."""

from __future__ import annotations

from PySide6.QtCore import QThread
from shiboken6 import isValid


def qalive(obj: object | None) -> bool:
    """Return ``True`` when *obj* is a non-deleted Qt wrapper."""
    if obj is None:
        return False
    try:
        return bool(isValid(obj))
    except RuntimeError:
        return False


def qthread_running(thread: QThread | None) -> bool:
    """Best-effort running check that tolerates deleted Qt objects."""
    if not qalive(thread):
        return False
    try:
        return bool(thread.isRunning())
    except RuntimeError:
        return False


def stop_thread(thread: QThread | None, wait_ms: int = 1500) -> None:
    """Stop a QThread safely if still alive and running."""
    if not qalive(thread):
        return
    try:
        if thread.isRunning():
            thread.quit()
            thread.wait(wait_ms)
    except RuntimeError:
        return
