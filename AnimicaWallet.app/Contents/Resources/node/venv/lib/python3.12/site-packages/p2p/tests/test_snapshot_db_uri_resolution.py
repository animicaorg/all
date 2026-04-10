from pathlib import Path

from p2p.node.p2p_service import P2PService


def test_resolve_db_uri_ignores_empty_env(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("ANIMICA_DB_URI", "")
    service = P2PService(chain_id=1, peerstore_path=tmp_path / "p2p")
    db_uri = service._resolve_db_uri()
    assert db_uri is not None
    assert db_uri.startswith("sqlite:///")
    assert Path(db_uri.replace("sqlite:///", "")).name
