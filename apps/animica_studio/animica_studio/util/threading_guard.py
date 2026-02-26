from __future__ import annotations

import logging

from PySide6.QtCore import QThread
from PySide6.QtWidgets import QApplication

log = logging.getLogger(__name__)


def assert_ui_thread() -> bool:
    app = QApplication.instance()
    if app is None:
        return True
    is_ui = QThread.currentThread() == app.thread()
    if not is_ui:
        log.error("UI-thread violation: current=%s expected=%s", QThread.currentThread(), app.thread())
    return is_ui
