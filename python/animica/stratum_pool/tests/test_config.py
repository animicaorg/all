import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from animica.stratum_pool.config import PoolConfig, load_config_from_env


def test_load_config_from_env_defaults(monkeypatch):
    monkeypatch.delenv("ANIMICA_STRATUM_HOST", raising=False)
    monkeypatch.delenv("ANIMICA_NETWORK", raising=False)
    monkeypatch.delenv("ANIMICA_STRATUM_RPC_TIMEOUT", raising=False)
    monkeypatch.delenv("ANIMICA_RPC_TIMEOUT", raising=False)
    monkeypatch.setenv(
        "ANIMICA_POOL_ADDRESS",
        "anim1qqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqq",
    )
    cfg = load_config_from_env()
    assert isinstance(cfg, PoolConfig)
    assert cfg.host == "0.0.0.0"
    assert cfg.port == 3333
    assert cfg.network == "mainnet"
    assert cfg.rpc_timeout == 15.0
    assert cfg.pool_mode == "pps"


def test_load_config_from_env_overrides(monkeypatch):
    monkeypatch.setenv("ANIMICA_STRATUM_HOST", "127.0.0.1")
    monkeypatch.setenv("ANIMICA_STRATUM_PORT", "9999")
    monkeypatch.setenv("ANIMICA_RPC_URL", "http://rpc.test/rpc")
    monkeypatch.setenv("ANIMICA_STRATUM_RPC_TIMEOUT", "22.5")
    monkeypatch.setenv("ANIMICA_CHAIN_ID", "7")
    monkeypatch.setenv(
        "ANIMICA_POOL_ADDRESS",
        "anim1qqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqq",
    )
    monkeypatch.setenv("ANIMICA_NETWORK", "testnet")
    monkeypatch.setenv("ANIMICA_POOL_MODE", "solo")
    cfg = load_config_from_env(
        overrides={"min_difficulty": 0.5, "max_difficulty": 0.75}
    )
    assert cfg.host == "127.0.0.1"
    assert cfg.port == 9999
    assert cfg.rpc_url == "http://rpc.test/rpc"
    assert cfg.rpc_timeout == 22.5
    assert cfg.chain_id == 7
    assert cfg.pool_address == "anim1qqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqq"
    assert cfg.network == "testnet"
    assert cfg.min_difficulty == 0.5
    assert cfg.max_difficulty == 0.75
    assert cfg.pool_mode == "solo"


def test_invalid_pool_mode(monkeypatch):
    monkeypatch.setenv(
        "ANIMICA_POOL_ADDRESS",
        "anim1qqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqq",
    )
    monkeypatch.setenv("ANIMICA_POOL_MODE", "pplns")
    with pytest.raises(ValueError):
        load_config_from_env()


def test_missing_pool_address(monkeypatch):
    monkeypatch.delenv("ANIMICA_POOL_ADDRESS", raising=False)
    with pytest.raises(ValueError):
        load_config_from_env()


def test_invalid_difficulty(monkeypatch):
    monkeypatch.setenv(
        "ANIMICA_POOL_ADDRESS",
        "anim1qqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqq",
    )
    monkeypatch.setenv("ANIMICA_STRATUM_MIN_DIFFICULTY", "-1")
    try:
        load_config_from_env()
    except ValueError:
        pass
    else:
        raise AssertionError("expected ValueError for negative difficulty")


def test_invalid_rpc_timeout(monkeypatch):
    monkeypatch.setenv(
        "ANIMICA_POOL_ADDRESS",
        "anim1qqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqq",
    )
    monkeypatch.setenv("ANIMICA_STRATUM_RPC_TIMEOUT", "0")
    with pytest.raises(ValueError):
        load_config_from_env()


def test_invalid_max_difficulty_non_positive(monkeypatch):
    monkeypatch.setenv(
        "ANIMICA_POOL_ADDRESS",
        "anim1qqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqq",
    )
    monkeypatch.setenv("ANIMICA_STRATUM_MAX_DIFFICULTY", "0")
    with pytest.raises(ValueError):
        load_config_from_env()


def test_mixed_difficulty_units_allowed(monkeypatch):
    monkeypatch.setenv(
        "ANIMICA_POOL_ADDRESS",
        "anim1qqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqq",
    )
    cfg = load_config_from_env(
        overrides={"min_difficulty": 15_000_000, "max_difficulty": 1.0}
    )
    assert cfg.min_difficulty == 15_000_000
    assert cfg.max_difficulty == 1.0
