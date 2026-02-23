from __future__ import annotations

import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from animica_studio.services.ena_automation_service import EnaService
from animica_studio.services.ena_store import EnaStore
from animica_studio.storage.config import Config
from animica_studio.ui.pages.publish_page import PublishPage


def _app() -> QApplication:
    return QApplication.instance() or QApplication([])  # type: ignore[return-value]


def _mk_service(tmp_path: Path) -> EnaService:
    svc = EnaService(Config(), EnaStore(tmp_path / "ena_store.json"))
    svc.store.set("checkpoints", [{"id": "x", "sha256": "abc123"}])
    return svc


def test_publish_page_shows_actionable_retry_on_da_policy_failure(tmp_path: Path, monkeypatch) -> None:
    _app()
    svc = _mk_service(tmp_path)
    monkeypatch.setattr(
        svc.da_status,
        "get_status",
        lambda *_a, **_k: {
            "enabled": True,
            "allow_remote_put": False,
            "configured_dir": "/data/da",
            "rpc_url": "http://127.0.0.1:8545/rpc",
            "raw": {"version": "1.0.0"},
            "configure_param_spec": [{"name": "allow_remote_put", "required": False}],
        },
    )

    page = PublishPage(svc)
    page._run()

    assert page.enable_remote_btn.isEnabled()
    assert page.copy_diag_btn.isEnabled()
    assert "Retry 'Push to DA'" in page.out.toPlainText() or "Retry Push to DA" in page.out.toPlainText()
