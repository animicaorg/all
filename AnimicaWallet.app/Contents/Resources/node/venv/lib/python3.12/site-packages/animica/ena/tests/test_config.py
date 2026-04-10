from __future__ import annotations

import os
from pathlib import Path

from animica.ena.config import load_ena_config, save_default_config


def test_config_precedence_and_env_override(tmp_path: Path, monkeypatch) -> None:
    user_home = tmp_path / "home"
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True)

    monkeypatch.setenv("ANIMICA_ENA_HOME", str(user_home))

    save_default_config(user_home / "config.toml")
    (workspace / ".animica" / "ena").mkdir(parents=True)
    (workspace / ".animica" / "ena" / "config.toml").write_text(
        """
[network]
allow_domains = ["workspace.example"]
max_requests = 7
""",
        encoding="utf-8",
    )

    explicit = tmp_path / "explicit.toml"
    explicit.write_text(
        """
[network]
allow_domains = ["explicit.example"]
max_requests = 11
""",
        encoding="utf-8",
    )

    config = load_ena_config(cwd=workspace, explicit_path=explicit)
    assert config.network.allow_domains == ["explicit.example"]
    assert config.network.max_requests == 11

    monkeypatch.setenv("ANIMICA_ENA_MAX_REQUESTS", "13")
    config_with_env = load_ena_config(cwd=workspace, explicit_path=explicit)
    assert config_with_env.network.max_requests == 13
