import os

from p2p import config as p2p_config
from p2p.cli import listen as listen_cli


def test_default_seeds_when_unset(monkeypatch):
    monkeypatch.delenv("ANIMICA_P2P_SEEDS", raising=False)
    cfg = p2p_config.load_config()
    assert cfg.seeds == p2p_config.DEFAULT_SEEDS

    args = listen_cli.argparse.Namespace(
        db=None,
        chain_id=None,
        listen=[],
        seed=[],
        enable_quic=None,
        enable_ws=None,
        nat=None,
        log_level=None,
    )
    listen_cfg = listen_cli._load_default_listen_config(args)
    assert listen_cfg.seeds == list(p2p_config.DEFAULT_SEEDS)


def test_env_seeds_override(monkeypatch):
    monkeypatch.setenv("ANIMICA_P2P_SEEDS", "/ip4/1.2.3.4/tcp/1234,/ip4/5.6.7.8/tcp/9876")
    cfg = p2p_config.load_config()
    assert cfg.seeds == ("/ip4/1.2.3.4/tcp/1234", "/ip4/5.6.7.8/tcp/9876")

    args = listen_cli.argparse.Namespace(
        db=None,
        chain_id=None,
        listen=[],
        seed=[],
        enable_quic=None,
        enable_ws=None,
        nat=None,
        log_level=None,
    )
    listen_cfg = listen_cli._load_default_listen_config(args)
    assert listen_cfg.seeds == ["/ip4/1.2.3.4/tcp/1234", "/ip4/5.6.7.8/tcp/9876"]
