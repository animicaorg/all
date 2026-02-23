from __future__ import annotations

import os
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from animica_studio.storage.config import Config
from animica_studio.ui.pages.quantum_page import QuantumPage


def _app() -> QApplication:
    return QApplication.instance() or QApplication([])  # type: ignore[return-value]


def _wait_until(predicate, timeout_s: float = 2.0) -> None:
    app = _app()
    start = time.time()
    while time.time() - start < timeout_s:
        app.processEvents()
        if predicate():
            return
        time.sleep(0.01)
    raise AssertionError("condition not met before timeout")


def test_quantum_page_instantiates_without_crash() -> None:
    _app()
    page = QuantumPage(Config())
    assert page is not None
    page.close()


def test_quantum_status_success_reenables_buttons(monkeypatch) -> None:
    _app()
    page = QuantumPage(Config())

    monkeypatch.setattr(page._service, "get_status", lambda: {"ok": True, "data": {"healthy": True}})
    page._on_refresh_status_clicked()

    _wait_until(lambda: not page._busy)
    assert "healthy" in page._status_output.toPlainText()
    assert page._action_buttons["refresh_status"].isEnabled()
    page.close()


def test_quantum_status_failure_reenables_buttons(monkeypatch) -> None:
    _app()
    page = QuantumPage(Config())

    monkeypatch.setattr(page._service, "get_status", lambda: {"ok": False, "error": "boom"})
    page._on_refresh_status_clicked()

    _wait_until(lambda: not page._busy)
    assert "Error:" in page._status_output.toPlainText()
    assert page._action_buttons["refresh_status"].isEnabled()
    page.close()
