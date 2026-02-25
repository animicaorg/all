from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QMessageBox

from animica_studio.storage.config import Config
from animica_studio.ui.pages.train_page import TrainPage


def _app() -> QApplication:
    return QApplication.instance() or QApplication([])  # type: ignore[return-value]


def test_train_page_init_and_guard_actions_without_plan(monkeypatch) -> None:
    _app()
    monkeypatch.setenv("ANIMICA_STUDIO_SAFE_MODE", "1")

    messages: list[str] = []

    def _capture(_parent, _title: str, text: str) -> int:
        messages.append(text)
        return int(QMessageBox.StandardButton.Ok)

    monkeypatch.setattr(QMessageBox, "information", _capture)

    cfg = Config()
    page = TrainPage(cfg)

    page._approve_plan()
    page._run_improvement_cycle()

    assert "No plan to approve. Generate a plan first." in messages
    assert "No plan available. Generate a plan first." in messages


def test_train_page_uses_null_evolution_when_disabled(monkeypatch) -> None:
    _app()
    monkeypatch.setenv("ANIMICA_STUDIO_SAFE_MODE", "1")

    cfg = Config()
    cfg.ena["enable_dataset_evolution"] = False

    page = TrainPage(cfg)

    assert page._evolution.enabled is False  # noqa: SLF001
    assert page.preview_plan_btn.isVisible() is False
    assert page.approve_plan_btn.isVisible() is False
    assert page.run_cycle_btn.isVisible() is False
