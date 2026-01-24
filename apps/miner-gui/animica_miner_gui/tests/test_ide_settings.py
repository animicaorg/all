"""Tests for IDE settings persistence."""

from __future__ import annotations

from animica_miner_gui.ide.settings import IDESettings, load_ide_settings, save_ide_settings


def test_ide_settings_roundtrip(tmp_path, monkeypatch) -> None:
    settings_path = tmp_path / "ide.json"

    monkeypatch.setattr(
        "animica_miner_gui.ide.settings.ide_settings_path",
        lambda: settings_path,
    )

    original = IDESettings(
        recent_projects=["/tmp/one", "/tmp/two"],
        last_workspace="/tmp/one",
        open_files=["/tmp/one/contract.py"],
        active_file="/tmp/one/contract.py",
        autosave_enabled=False,
        autosave_interval_ms=2000,
        explorer_url="https://explorer.example.com",
    )
    save_ide_settings(original)
    loaded = load_ide_settings()

    assert loaded.to_dict() == original.to_dict()
