"""Central tracker for active QThreads and safe shutdown."""

from __future__ import annotations

import time

from PySide6.QtCore import QObject, QThread
from PySide6.QtWidgets import QApplication

from animica_studio.util.qt import qalive


class ShutdownManager(QObject):
    _instance: "ShutdownManager | None" = None

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._threads: set[QThread] = set()
        app = QApplication.instance()
        if app is not None:
            app.aboutToQuit.connect(self.shutdown)

    @classmethod
    def instance(cls) -> "ShutdownManager":
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def track_thread(self, thread: QThread | None) -> None:
        if thread is None or not qalive(thread):
            return
        self._threads.add(thread)
        thread.finished.connect(lambda: self._threads.discard(thread))

    def shutdown(self) -> None:
        active = [t for t in list(self._threads) if qalive(t)]
        for thread in active:
            if thread.isRunning():
                thread.quit()

        deadline = time.monotonic() + 1.5
        while time.monotonic() < deadline:
            running = [t for t in active if qalive(t) and t.isRunning()]
            if not running:
                break
            app = QApplication.instance()
            if app is None:
                break
            app.processEvents()

        for thread in active:
            if qalive(thread) and thread.isRunning():
                thread.wait(300)
