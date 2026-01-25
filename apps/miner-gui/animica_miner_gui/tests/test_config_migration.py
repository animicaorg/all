"""Tests for config migration and null rpc_url handling."""

import json
from pathlib import Path

import pytest

from animica_miner_gui.backend.config import (
    NetworkMode,
    load_config,
)


def _load_json(path: Path) -> dict:
    with open(path, "r", encoding="utf-8") as handle:
        return json.load(handle)


def test_load_config_missing_rpc_url(tmp_path):
    """Config missing rpc_url should load and default to local mode."""
    config_path = tmp_path / "config.json"
    config_path.write_text(
        json.dumps({"version": "1.0", "network": {"network_type": "devnet"}}),
        encoding="utf-8",
    )

    config = load_config(config_path)

    assert config.network.mode == NetworkMode.LOCAL
    assert config.network.rpc_url is None
    assert config.network.external_rpc_url is None


def test_load_config_null_rpc_url_repro(tmp_path):
    """Config with rpc_url=null should migrate without crashing."""
    repro_path = Path(__file__).parent / "data" / "repro_config_null_rpc.json"
    config_path = tmp_path / "config.json"
    config_path.write_text(repro_path.read_text(encoding="utf-8"), encoding="utf-8")

    config = load_config(config_path)

    assert config.network.mode == NetworkMode.LOCAL
    assert config.network.rpc_url is None

    repaired = _load_json(config_path)
    assert repaired["network"]["mode"] == NetworkMode.LOCAL.value
    assert repaired["network"]["rpc_url"] is None


def test_load_config_migrates_external_rpc(tmp_path):
    """Old rpc_url string should migrate to external mode."""
    config_path = tmp_path / "config.json"
    config_path.write_text(
        json.dumps(
            {
                "version": "1.0",
                "network": {"network_type": "mainnet", "rpc_url": "https://rpc.example.com"},
            }
        ),
        encoding="utf-8",
    )

    config = load_config(config_path)

    assert config.network.mode == NetworkMode.EXTERNAL
    assert config.network.external_rpc_url == "https://rpc.example.com"

    repaired = _load_json(config_path)
    assert repaired["network"]["mode"] == NetworkMode.EXTERNAL.value
    assert repaired["network"]["external_rpc_url"] == "https://rpc.example.com"
