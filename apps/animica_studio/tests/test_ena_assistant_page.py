from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from animica_studio.storage.config import Config
from animica_studio.ui.pages.ena_page import EnaPage


def _app() -> QApplication:
    return QApplication.instance() or QApplication([])  # type: ignore[return-value]


def test_ena_assistant_page_loads_saved_runtime_settings() -> None:
    _app()
    cfg = Config()
    cfg.ena["mode"] = "remote_http"
    cfg.ena["endpoint"] = "https://ena.example.test"
    cfg.ena["allow_modify_files"] = True
    cfg.ena["allow_exec"] = False

    page = EnaPage(cfg)

    assert page._mode.currentText() == "remote_http"
    assert page._endpoint.text() == "https://ena.example.test"
    assert page._allow_modify_files.isChecked() is True
    assert page._allow_exec.isChecked() is False
    page.close()
