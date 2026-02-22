"""Qt lifetime helpers for safe QObject/QThread access."""

from __future__ import annotations

import functools
import logging
from collections.abc import Callable
from typing import ParamSpec, TypeVar

from PySide6.QtCore import QThread
from PySide6.QtWidgets import QApplication
from shiboken6 import isValid

P = ParamSpec("P")
R = TypeVar("R")


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


def safe_slot(
    logger: logging.Logger,
    *,
    message: str = "Unhandled exception in Qt slot",
) -> Callable[[Callable[P, R]], Callable[P, R | None]]:
    """Decorator that logs exceptions from Qt slot handlers and swallows them."""

    def decorator(fn: Callable[P, R]) -> Callable[P, R | None]:
        @functools.wraps(fn)
        def wrapped(*args: P.args, **kwargs: P.kwargs) -> R | None:
            try:
                return fn(*args, **kwargs)
            except Exception:  # noqa: BLE001
                logger.exception("%s: %s", message, fn.__qualname__)
                return None

        return wrapped

    return decorator


def ui_thread_only(
    logger: logging.Logger,
    *,
    message: str = "UI slot invoked off the main Qt thread",
) -> Callable[[Callable[P, R]], Callable[P, R | None]]:
    """Decorator that blocks UI slot execution when called off GUI thread."""

    def decorator(fn: Callable[P, R]) -> Callable[P, R | None]:
        @functools.wraps(fn)
        def wrapped(*args: P.args, **kwargs: P.kwargs) -> R | None:
            app = QApplication.instance()
            if app is not None and QThread.currentThread() != app.thread():
                logger.error("%s: %s", message, fn.__qualname__)
                return None
            try:
                return fn(*args, **kwargs)
            except Exception:  # noqa: BLE001
                logger.exception("Unhandled exception in guarded UI slot: %s", fn.__qualname__)
                return None

        return wrapped

    return decorator
