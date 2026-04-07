from __future__ import annotations

import logging

try:
    from PySide6.QtCore import QThread
    from PySide6.QtWidgets import QApplication
except ImportError:
    QThread = None  # type: ignore[assignment]
    QApplication = None  # type: ignore[assignment]

log = logging.getLogger(__name__)


def assert_ui_thread() -> bool:
    if QApplication is None or QThread is None:
        return True
    app = QApplication.instance()
    if app is None:
        return True
    is_ui = QThread.currentThread() == app.thread()
    if not is_ui:
        log.error("UI-thread violation: current=%s expected=%s", QThread.currentThread(), app.thread())
    return is_ui
