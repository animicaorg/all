from __future__ import annotations

from pathlib import Path

from rpc.tests import new_test_client, rpc_call


def test_import_peers_returns_schema_and_persists(tmp_path, monkeypatch) -> None:
    peerstore_root = tmp_path / "peerstore"
    monkeypatch.setenv("ANIMICA_PEER_STORE_PATH", str(peerstore_root))
    repo_root = Path(__file__).resolve().parents[2]
    monkeypatch.setenv("ANIMICA_GENESIS_PATH", str(repo_root / "core" / "genesis" / "mainnet.json"))

    client, _cfg, _tmp = new_test_client(tmpdir=str(tmp_path / "rpc"))
    response = rpc_call(client, "p2p.importPeers", [["seed.example:30333"]])
    result = response["result"]

    assert result["ok"] is True
    assert "imported" in result
    assert "skipped" in result
    assert "invalid" in result
    assert result["source"] == "rpc"
    assert result["store"]["db"]
    assert result["store"]["json"]

    from p2p.peer.peerstore import PeerStore

    store = PeerStore(Path(result["store"]["db"]))
    addrs = [addr for _, addr, _ in store.list_addresses(limit=100)]
    assert any("seed.example" in addr and "30333" in addr for addr in addrs)
