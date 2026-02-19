from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from animica_studio.storage.config import Config
from animica_studio.services.profile_service import ProfileService
from animica_studio.ui.main_window import MainWindow


def test_main_window_smoke() -> None:
    app = QApplication.instance() or QApplication([])
    cfg = Config()
    service = ProfileService(cfg)
    window = MainWindow(cfg, service)
    assert window.windowTitle() == "Animica Studio"
    window.close()
    app.quit()
