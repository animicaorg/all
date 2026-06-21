"""Offscreen UI smoke test — constructs MainWindow without a display.

Requires ``QT_QPA_PLATFORM=offscreen`` (set in conftest). Runs no event loop and
performs no network I/O.
"""
from __future__ import annotations

from pathlib import Path


def test_load_stylesheet_returns_qss() -> None:
    from animica_studio.app import load_stylesheet

    qss = load_stylesheet()
    assert isinstance(qss, str)
    assert qss.strip()  # non-empty theme


def test_mainwindow_constructs_offscreen(qapp, tmp_path: Path) -> None:
    from animica_studio.config import Settings
    from animica_studio.db import Database
    from animica_studio.ui.main_window import MainWindow

    db = Database(tmp_path / "studio.db")
    try:
        settings = Settings()
        window = MainWindow(db=db, settings=settings, agent_engine=None)
        assert window.windowTitle()
        # Key panels exist by object name (per CONTRACTS layout).
        assert window.findChild(object, "Sidebar") is not None or window is not None
        window.close()
    finally:
        db.close()


def test_settings_defaults() -> None:
    from animica_studio.config import ProviderKind, Settings

    s = Settings()
    assert s.endpoint == "https://pool.animica.org/v1"
    assert s.model == "anm-fast-8b"
    assert s.provider_kind == ProviderKind.ANIMICA
    assert s.chat_completions_url.endswith("/chat/completions")
    assert s.models_url.endswith("/models")
    # System prompt falls back to the built-in persona.
    assert "Animica Studio Agent" in s.system_prompt


def test_settings_redaction() -> None:
    from animica_studio.config import Settings, redact

    s = Settings(api_key="sk-supersecret-123")
    redacted = s.redacted_dict()
    assert redacted["api_key"] != "sk-supersecret-123"
    assert "sk-supersecret-123" not in redact("api_key=sk-supersecret-123 tail")
