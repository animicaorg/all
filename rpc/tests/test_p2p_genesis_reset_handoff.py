from pathlib import Path
from types import SimpleNamespace


def test_init_p2p_service_passes_shared_genesis_reset_policy(monkeypatch) -> None:
    from rpc import deps

    recorded: dict[str, object] = {}

    class DummyP2PConfig:
        listen_tcp = ("127.0.0.1", 30333)
        seeds = []

    class DummyP2PDeps:
        @staticmethod
        def open(*args, **kwargs):
            recorded["args"] = args
            recorded["kwargs"] = kwargs
            return object()

    class DummyP2PService:
        def __init__(self, **_kwargs):
            pass

    monkeypatch.setenv("ANIMICA_AUTO_RESET_GENESIS_MISMATCH", "1")
    monkeypatch.setenv("ANIMICA_P2P_ENABLE", "1")
    monkeypatch.setenv("ANIMICA_P2P_REQUIRED", "0")

    import p2p
    import p2p.config
    import p2p.deps
    import p2p.node.p2p_service

    monkeypatch.setattr(p2p.config, "load_config", lambda: DummyP2PConfig())
    monkeypatch.setattr(p2p.node.p2p_service, "P2PService", DummyP2PService)
    monkeypatch.setattr(p2p, "register_service", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(p2p.deps, "P2PDeps", DummyP2PDeps)
    monkeypatch.setattr(p2p.deps, "AsyncP2PDeps", lambda sync: sync)

    cfg = SimpleNamespace(
        db_uri="sqlite:////tmp/animica-test.db",
        chain_id=1,
        genesis_path="/tmp/genesis.json",
        log_level="INFO",
        p2p_required=False,
    )

    deps._init_p2p_service(cfg, Path("/tmp"), None)

    assert recorded["args"] == ("sqlite:////tmp/animica-test.db", "/tmp/genesis.json")
    assert recorded["kwargs"] == {"allow_genesis_reset": True}
