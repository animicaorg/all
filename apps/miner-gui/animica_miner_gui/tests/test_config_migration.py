"""Tests for config migration and null rpc_url handling."""

import json
from pathlib import Path

import pytest

from animica_miner_gui.backend.config import load_config


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

    assert config.network.rpc_url is None


def test_load_config_null_rpc_url_repro(tmp_path):
    """Config with rpc_url=null should migrate without crashing."""
    repro_path = Path(__file__).parent / "data" / "repro_config_null_rpc.json"
    config_path = tmp_path / "config.json"
    config_path.write_text(repro_path.read_text(encoding="utf-8"), encoding="utf-8")

    config = load_config(config_path)

    # No explicit override in the fixture...
    assert config.network.rpc_url is None
    # ...but it must resolve to the endpoint for its network rather than to
    # nothing at all, which is what left every surface showing "--".
    from animica_miner_gui.backend.config import DEFAULT_RPC_ENDPOINTS
    assert config.network.resolved_rpc_url() == DEFAULT_RPC_ENDPOINTS[config.network.network_type]


def test_load_config_preserves_external_rpc(tmp_path):
    """A user-configured remote rpc_url must survive a load.

    It used to be deleted on every launch ("the GUI now uses the bundled node
    only"), which made it impossible to point the app at any endpoint — and
    packaged builds have no bundled node to fall back to.
    """
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

    assert config.network.rpc_url == "https://rpc.example.com"
    assert config.network.resolved_rpc_url() == "https://rpc.example.com"

    # And it must still be on disk afterwards.
    repaired = _load_json(config_path)
    assert repaired["network"]["rpc_url"] == "https://rpc.example.com"
