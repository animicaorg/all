from __future__ import annotations

from pathlib import Path

import pytest

from animica.config import load_dotenv, parse_env_bool, resolve_bootstrap_mode


@pytest.mark.parametrize(
    ("raw", "default", "expected"),
    [
        ("true", False, True),
        ("1", False, True),
        ("yes", False, True),
        ("on", False, True),
        ("false", True, False),
        ("0", True, False),
        ("no", True, False),
        ("off", True, False),
        ("", True, True),
        (None, False, False),
    ],
)
def test_parse_env_bool(raw: str | None, default: bool, expected: bool) -> None:
    assert parse_env_bool(raw, default) is expected


def test_resolve_bootstrap_mode_env_false(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text("", encoding="utf-8")
    load_dotenv(env_file, force=True)
    monkeypatch.setenv("ANIMICA_BOOTSTRAP_NODE", "false")

    setting = resolve_bootstrap_mode()

    assert setting.value is False
    assert setting.source == "process_env"


def test_resolve_bootstrap_mode_default_false(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text("", encoding="utf-8")
    monkeypatch.delenv("ANIMICA_BOOTSTRAP_NODE", raising=False)
    load_dotenv(env_file, force=True)

    setting = resolve_bootstrap_mode()

    assert setting.value is False
    assert setting.source == "default"


def test_resolve_bootstrap_mode_dotenv_true(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.delenv("ANIMICA_BOOTSTRAP_NODE", raising=False)
    env_file = tmp_path / ".env"
    env_file.write_text("ANIMICA_BOOTSTRAP_NODE=true\n", encoding="utf-8")
    load_dotenv(env_file, force=True)

    setting = resolve_bootstrap_mode()

    assert setting.value is True
    assert setting.source == "dotenv"
