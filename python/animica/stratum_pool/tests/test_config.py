import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from animica.stratum_pool.config import PoolConfig, load_config_from_env


def test_load_config_from_env_defaults(monkeypatch):
    monkeypatch.delenv("ANIMICA_STRATUM_HOST", raising=False)
    monkeypatch.delenv("ANIMICA_NETWORK", raising=False)
    cfg = load_config_from_env()
    assert isinstance(cfg, PoolConfig)
    assert cfg.host == "0.0.0.0"
    assert cfg.port == 3333
    assert cfg.network == "devnet"


def test_load_config_from_env_overrides(monkeypatch):
    monkeypatch.setenv("ANIMICA_STRATUM_HOST", "127.0.0.1")
    monkeypatch.setenv("ANIMICA_STRATUM_PORT", "9999")
    monkeypatch.setenv("ANIMICA_RPC_URL", "http://rpc.test/rpc")
    monkeypatch.setenv("ANIMICA_CHAIN_ID", "7")
    monkeypatch.setenv("ANIMICA_POOL_ADDRESS", "animica1pool")
    monkeypatch.setenv("ANIMICA_NETWORK", "testnet")
    cfg = load_config_from_env(
        overrides={"min_difficulty": 0.5, "max_difficulty": 0.75}
    )
    assert cfg.host == "127.0.0.1"
    assert cfg.port == 9999
    assert cfg.rpc_url == "http://rpc.test/rpc"
    assert cfg.chain_id == 7
    assert cfg.pool_address == "animica1pool"
    assert cfg.network == "testnet"
    assert cfg.min_difficulty == 0.5
    assert cfg.max_difficulty == 0.75


def test_invalid_difficulty(monkeypatch):
    monkeypatch.setenv("ANIMICA_STRATUM_MIN_DIFFICULTY", "-1")
    try:
        load_config_from_env()
    except ValueError:
        pass
    else:
        raise AssertionError("expected ValueError for negative difficulty")
