from __future__ import annotations

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PySide6.QtWidgets", reason="PySide6 not installed")

from PySide6.QtWidgets import QApplication, QFileDialog

from animica_studio.storage.config import Config
from animica_studio.ui.pages import ide_page as ide_page_mod


def _app() -> QApplication:
    return QApplication.instance() or QApplication([])  # type: ignore[return-value]


def test_ide_page_restores_workspace_from_config(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    _app()
    ws = tmp_path / "workspace"
    ws.mkdir()

    cfg = Config(ide_workspace_root=str(ws))
    monkeypatch.setattr(ide_page_mod, "load_config", lambda: cfg)
    monkeypatch.setattr(ide_page_mod, "_try_import_webengine", lambda: (None, None, None))

    page = ide_page_mod.IdePage()
    assert page._svc.workspace == ws.resolve()
    assert page._ws_label.text() == str(ws)
    assert page._tree.topLevelItemCount() == 1


def test_ide_page_change_workspace_persists_selection(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    _app()
    ws = tmp_path / "workspace"
    ws.mkdir()

    cfg = Config()
    saved: list[str | None] = []

    monkeypatch.setattr(ide_page_mod, "load_config", lambda: cfg)
    monkeypatch.setattr(ide_page_mod, "_try_import_webengine", lambda: (None, None, None))
    monkeypatch.setattr(ide_page_mod, "save_config", lambda c: saved.append(c.ide_workspace_root))
    monkeypatch.setattr(QFileDialog, "getExistingDirectory", lambda *args, **kwargs: str(ws))

    page = ide_page_mod.IdePage()
    page._on_change_workspace()

    assert cfg.ide_workspace_root == str(ws)
    assert cfg.workspace_root == str(ws)
    assert saved == [str(ws)]
    assert page._svc.workspace == ws.resolve()
