from pathlib import Path

from rpc import config as rpc_config


def test_devnet_config_uses_genesis_chain_id(monkeypatch, tmp_path):
    genesis_src = (
        Path(__file__).resolve().parents[2] / "genesis" / "genesis.sample.devnet.json"
    )
    genesis_copy = tmp_path / "devnet.genesis.json"
    genesis_copy.write_text(genesis_src.read_text())

    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("ANIMICA_NETWORK", "devnet")
    monkeypatch.setenv(
        "ANIMICA_RPC_DB_URI", "sqlite:////${HOME}/animica/devnet/chain.db"
    )
    monkeypatch.setenv("ANIMICA_GENESIS_PATH", str(genesis_copy))
    monkeypatch.delenv("ANIMICA_CHAIN_ID", raising=False)

    cfg = rpc_config.load()

    expected_db = f"sqlite:////{tmp_path}/animica/devnet/chain.db"
    assert cfg.db_uri == expected_db
    assert cfg.chain_id == 1337
    assert cfg.genesis_path == genesis_copy
