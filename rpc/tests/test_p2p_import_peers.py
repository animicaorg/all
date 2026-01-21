from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

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


@pytest.mark.asyncio
async def test_import_peers_handles_legacy_success_field() -> None:
    """Test that import_peers RPC correctly maps 'success' field to 'ok' field.
    
    The P2P service returns {"success": True, "added": N} but the RPC should
    normalize this to {"ok": True, "imported": N} for consistency.
    """
    from rpc.methods.p2p import import_peers
    from unittest.mock import patch
    
    # Mock P2P service response with legacy field names
    mock_service = MagicMock()
    mock_service.import_peers = AsyncMock(return_value={
        "success": True,  # Legacy field name
        "added": 5,       # Legacy field name (should map to "imported")
        "skipped": 2,
        "invalid": 1,
        "dial_attempted": 5,
        "dial_success": 3,
        "errors": []
    })
    
    with patch('rpc.methods.p2p._get_p2p_service', return_value=mock_service):
        with patch('rpc.methods.p2p._resolve_peer_store_paths', return_value={"db": "/tmp/db", "json": "/tmp/json"}):
            with patch('rpc.methods.p2p._peer_counts_snapshot', return_value={"peers_total": 3, "peers_inbound": 0, "peers_outbound": 3}):
                result = await import_peers(addresses=["peer1:30333", "peer2:30333"])
    
    # Verify the RPC correctly mapped legacy fields
    assert result["ok"] is True, "RPC should map 'success' to 'ok'"
    assert result["imported"] == 5, "RPC should map 'added' to 'imported'"
    assert result["skipped"] == 2
    assert result["invalid"] == 1


@pytest.mark.asyncio
async def test_import_peers_handles_new_ok_field() -> None:
    """Test that import_peers RPC works with new 'ok' and 'imported' fields."""
    from rpc.methods.p2p import import_peers
    from unittest.mock import patch
    
    # Mock P2P service response with new field names
    mock_service = MagicMock()
    mock_service.import_peers = AsyncMock(return_value={
        "ok": True,       # New field name
        "imported": 3,    # New field name
        "skipped": 0,
        "invalid": 0,
        "dial_attempted": 3,
        "dial_success": 2,
        "errors": []
    })
    
    with patch('rpc.methods.p2p._get_p2p_service', return_value=mock_service):
        with patch('rpc.methods.p2p._resolve_peer_store_paths', return_value={"db": "/tmp/db", "json": "/tmp/json"}):
            with patch('rpc.methods.p2p._peer_counts_snapshot', return_value={"peers_total": 2, "peers_inbound": 0, "peers_outbound": 2}):
                result = await import_peers(addresses=["peer1:30333"])
    
    # Verify the RPC works with new fields
    assert result["ok"] is True
    assert result["imported"] == 3
