"""Tests for peer CLI commands."""

from __future__ import annotations

from typing import Any

import httpx
import respx
from animica.cli import peer
from typer.testing import CliRunner

runner = CliRunner()


@respx.mock
def test_list_peers_success(monkeypatch: Any) -> None:
    """Test listing peers successfully."""
    rpc_url = "http://localhost:9999/rpc"
    monkeypatch.setenv("ANIMICA_RPC_URL", rpc_url)

    mock_peers = [
        {
            "id": "QmPeer1",
            "addr": "/ip4/1.2.3.4/tcp/30303",
            "status": "connected",
        },
        {
            "id": "QmPeer2",
            "addr": "/ip4/5.6.7.8/tcp/30303",
            "status": "connected",
        },
    ]

    # Mock the RPC call
    respx.post(rpc_url).mock(
        return_value=httpx.Response(
            200,
            json={"jsonrpc": "2.0", "id": 1, "result": mock_peers},
        )
    )

    result = runner.invoke(peer.app, ["list"])
    assert result.exit_code == 0
    assert "Connected Peers: 2" in result.output
    assert "QmPeer1" in result.output
    assert "QmPeer2" in result.output


@respx.mock
def test_list_peers_verbose(monkeypatch: Any) -> None:
    """Test listing peers with verbose output."""
    rpc_url = "http://localhost:9999/rpc"
    monkeypatch.setenv("ANIMICA_RPC_URL", rpc_url)

    mock_peers = [
        {
            "id": "QmPeer1",
            "addr": "/ip4/1.2.3.4/tcp/30303",
            "status": "connected",
        }
    ]

    respx.post(rpc_url).mock(
        return_value=httpx.Response(
            200,
            json={"jsonrpc": "2.0", "id": 1, "result": mock_peers},
        )
    )

    result = runner.invoke(peer.app, ["list", "--verbose"])
    assert result.exit_code == 0
    # Verbose mode should show JSON output
    assert '"id": "QmPeer1"' in result.output or '"id":"QmPeer1"' in result.output


@respx.mock
def test_list_peers_empty(monkeypatch: Any) -> None:
    """Test listing peers when no peers are connected."""
    rpc_url = "http://localhost:9999/rpc"
    monkeypatch.setenv("ANIMICA_RPC_URL", rpc_url)

    respx.post(rpc_url).mock(
        return_value=httpx.Response(
            200,
            json={"jsonrpc": "2.0", "id": 1, "result": []},
        )
    )

    result = runner.invoke(peer.app, ["list"])
    assert result.exit_code == 0
    assert "No peers connected" in result.output


@respx.mock
def test_list_peers_rpc_unavailable(monkeypatch: Any) -> None:
    """Test listing peers when RPC is unavailable."""
    rpc_url = "http://localhost:9999/rpc"
    monkeypatch.setenv("ANIMICA_RPC_URL", rpc_url)

    # Mock all potential RPC methods to fail
    respx.post(rpc_url).mock(
        return_value=httpx.Response(
            200,
            json={"jsonrpc": "2.0", "id": 1, "error": {"code": -32601, "message": "Method not found"}},
        )
    )

    result = runner.invoke(peer.app, ["list"])
    assert result.exit_code == 1
    assert "Unable to retrieve peers" in result.output


@respx.mock
def test_add_peer_success(monkeypatch: Any) -> None:
    """Test adding a peer successfully."""
    rpc_url = "http://localhost:9999/rpc"
    monkeypatch.setenv("ANIMICA_RPC_URL", rpc_url)

    respx.post(rpc_url).mock(
        return_value=httpx.Response(
            200,
            json={"jsonrpc": "2.0", "id": 1, "result": True},
        )
    )

    result = runner.invoke(peer.app, ["add", "/ip4/1.2.3.4/tcp/30303/p2p/QmPeer1"])
    assert result.exit_code == 0
    assert "Successfully added peer" in result.output


@respx.mock
def test_add_peer_failure(monkeypatch: Any) -> None:
    """Test adding a peer when it fails."""
    rpc_url = "http://localhost:9999/rpc"
    monkeypatch.setenv("ANIMICA_RPC_URL", rpc_url)

    respx.post(rpc_url).mock(
        return_value=httpx.Response(
            200,
            json={"jsonrpc": "2.0", "id": 1, "error": {"code": -32000, "message": "Failed to add peer"}},
        )
    )

    result = runner.invoke(peer.app, ["add", "/ip4/1.2.3.4/tcp/30303/p2p/QmPeer1"])
    assert result.exit_code == 1
    assert "Failed to add peer" in result.output


@respx.mock
def test_remove_peer_success(monkeypatch: Any) -> None:
    """Test removing a peer successfully."""
    rpc_url = "http://localhost:9999/rpc"
    monkeypatch.setenv("ANIMICA_RPC_URL", rpc_url)

    respx.post(rpc_url).mock(
        return_value=httpx.Response(
            200,
            json={"jsonrpc": "2.0", "id": 1, "result": True},
        )
    )

    result = runner.invoke(peer.app, ["remove", "QmPeer1"])
    assert result.exit_code == 0
    assert "Successfully removed peer" in result.output


@respx.mock
def test_remove_peer_failure(monkeypatch: Any) -> None:
    """Test removing a peer when it fails."""
    rpc_url = "http://localhost:9999/rpc"
    monkeypatch.setenv("ANIMICA_RPC_URL", rpc_url)

    respx.post(rpc_url).mock(
        return_value=httpx.Response(
            200,
            json={"jsonrpc": "2.0", "id": 1, "error": {"code": -32000, "message": "Peer not found"}},
        )
    )

    result = runner.invoke(peer.app, ["remove", "QmPeer1"])
    assert result.exit_code == 1
    assert "Failed to remove peer" in result.output


@respx.mock
def test_peer_info_success(monkeypatch: Any) -> None:
    """Test getting peer info successfully."""
    rpc_url = "http://localhost:9999/rpc"
    monkeypatch.setenv("ANIMICA_RPC_URL", rpc_url)

    mock_peer_info = {
        "id": "QmPeer1",
        "addr": "/ip4/1.2.3.4/tcp/30303",
        "status": "connected",
        "latency": 50,
        "version": "1.0.0",
    }

    respx.post(rpc_url).mock(
        return_value=httpx.Response(
            200,
            json={"jsonrpc": "2.0", "id": 1, "result": mock_peer_info},
        )
    )

    result = runner.invoke(peer.app, ["info", "QmPeer1"])
    assert result.exit_code == 0
    assert "Peer Information: QmPeer1" in result.output
    assert "1.2.3.4" in result.output


@respx.mock
def test_peer_info_not_found(monkeypatch: Any) -> None:
    """Test getting peer info when peer not found."""
    rpc_url = "http://localhost:9999/rpc"
    monkeypatch.setenv("ANIMICA_RPC_URL", rpc_url)

    # Mock all methods to fail
    respx.post(rpc_url).mock(
        return_value=httpx.Response(
            200,
            json={"jsonrpc": "2.0", "id": 1, "error": {"code": -32000, "message": "Peer not found"}},
        )
    )

    result = runner.invoke(peer.app, ["info", "QmPeer1"])
    assert result.exit_code == 1
    assert "Unable to retrieve information" in result.output


@respx.mock
def test_list_peers_fallback_to_json_store(monkeypatch: Any, tmp_path: Any) -> None:
    """Test fallback to JSON peer store when RPC is unavailable."""
    import json
    
    rpc_url = "http://localhost:9999/rpc"
    monkeypatch.setenv("ANIMICA_RPC_URL", rpc_url)
    
    # Create a test peer store JSON file
    store_path = tmp_path / "peers.json"
    peers_data = {
        "peers": [
            {
                "peer_id": "peer123",
                "addrs": ["/ip4/10.0.0.1/tcp/30303"],
                "score": 10.5,
                "last_seen": 1234567890.0,
                "connected": False,
                "banned_until": None,
                "tags": {}
            },
            {
                "peer_id": "peer456",
                "addrs": ["/ip4/10.0.0.2/tcp/30303", "/ip4/10.0.0.3/tcp/30304"],
                "score": 5.0,
                "last_seen": 1234567891.0,
                "connected": True,
                "banned_until": None,
                "tags": {}
            }
        ]
    }
    store_path.write_text(json.dumps(peers_data))
    
    # Mock all RPC methods to fail
    respx.post(rpc_url).mock(
        return_value=httpx.Response(
            200,
            json={"jsonrpc": "2.0", "id": 1, "error": {"code": -32601, "message": "Method not found"}},
        )
    )
    
    result = runner.invoke(peer.app, ["list", "--store", str(store_path)])
    assert result.exit_code == 0
    assert "Connected Peers: 2" in result.output
    assert "from local peer store" in result.output
    assert "peer123" in result.output
    assert "peer456" in result.output
    assert "/ip4/10.0.0.1/tcp/30303" in result.output


@respx.mock
def test_list_peers_fallback_to_json_store_verbose(monkeypatch: Any, tmp_path: Any) -> None:
    """Test fallback to JSON peer store with verbose output."""
    import json
    
    rpc_url = "http://localhost:9999/rpc"
    monkeypatch.setenv("ANIMICA_RPC_URL", rpc_url)
    
    # Create a test peer store JSON file
    store_path = tmp_path / "peers.json"
    peers_data = {
        "peers": [
            {
                "peer_id": "peer789",
                "addrs": ["/ip4/192.168.1.1/tcp/42000"],
                "score": 15.0,
                "last_seen": 1234567892.0,
                "connected": True,
                "banned_until": None,
                "tags": {"role": "validator"}
            }
        ]
    }
    store_path.write_text(json.dumps(peers_data))
    
    # Mock all RPC methods to fail
    respx.post(rpc_url).mock(
        return_value=httpx.Response(
            200,
            json={"jsonrpc": "2.0", "id": 1, "error": {"code": -32601, "message": "Method not found"}},
        )
    )
    
    result = runner.invoke(peer.app, ["list", "--store", str(store_path), "--verbose"])
    assert result.exit_code == 0
    assert "Connected Peers: 1" in result.output
    assert "from local peer store" in result.output
    # Check for JSON output
    assert '"peer_id": "peer789"' in result.output or '"peer_id":"peer789"' in result.output


@respx.mock
def test_list_peers_fallback_empty_store(monkeypatch: Any, tmp_path: Any) -> None:
    """Test fallback when both RPC and store are empty."""
    import json
    
    rpc_url = "http://localhost:9999/rpc"
    monkeypatch.setenv("ANIMICA_RPC_URL", rpc_url)
    
    # Create an empty peer store JSON file
    store_path = tmp_path / "peers.json"
    peers_data = {"peers": []}
    store_path.write_text(json.dumps(peers_data))
    
    # Note: Even though store is empty, we don't error if the file exists
    # We only error if both RPC fails AND the store file doesn't exist
    
    # Mock all RPC methods to fail
    respx.post(rpc_url).mock(
        return_value=httpx.Response(
            200,
            json={"jsonrpc": "2.0", "id": 1, "error": {"code": -32601, "message": "Method not found"}},
        )
    )
    
    result = runner.invoke(peer.app, ["list", "--store", str(store_path)])
    assert result.exit_code == 0
    assert "No peers connected" in result.output


@respx.mock
def test_list_peers_fallback_nonexistent_store(monkeypatch: Any, tmp_path: Any) -> None:
    """Test fallback when store file does not exist."""
    rpc_url = "http://localhost:9999/rpc"
    monkeypatch.setenv("ANIMICA_RPC_URL", rpc_url)
    
    # Non-existent store path
    store_path = tmp_path / "nonexistent_peers.json"
    
    # Mock all RPC methods to fail
    respx.post(rpc_url).mock(
        return_value=httpx.Response(
            200,
            json={"jsonrpc": "2.0", "id": 1, "error": {"code": -32601, "message": "Method not found"}},
        )
    )
    
    result = runner.invoke(peer.app, ["list", "--store", str(store_path)])
    assert result.exit_code == 1
    assert "Unable to retrieve peers" in result.output
    assert str(store_path) in result.output


@respx.mock
def test_list_peers_rpc_takes_precedence_over_store(monkeypatch: Any, tmp_path: Any) -> None:
    """Test that RPC is tried first and takes precedence over store."""
    import json
    
    rpc_url = "http://localhost:9999/rpc"
    monkeypatch.setenv("ANIMICA_RPC_URL", rpc_url)
    
    # Create a peer store with different peers
    store_path = tmp_path / "peers.json"
    peers_data = {
        "peers": [
            {
                "peer_id": "store_peer",
                "addrs": ["/ip4/1.1.1.1/tcp/1111"],
                "score": 1.0,
                "last_seen": 1234567890.0,
                "connected": False,
            }
        ]
    }
    store_path.write_text(json.dumps(peers_data))
    
    # Mock RPC to succeed with different peers
    mock_rpc_peers = [
        {
            "id": "rpc_peer",
            "addr": "/ip4/2.2.2.2/tcp/2222",
            "status": "connected",
        }
    ]
    respx.post(rpc_url).mock(
        return_value=httpx.Response(
            200,
            json={"jsonrpc": "2.0", "id": 1, "result": mock_rpc_peers},
        )
    )
    
    result = runner.invoke(peer.app, ["list", "--store", str(store_path)])
    assert result.exit_code == 0
    # Should show RPC peers, not store peers
    assert "rpc_peer" in result.output
    assert "store_peer" not in result.output
    assert "from local peer store" not in result.output


@respx.mock
def test_list_peers_fallback_to_sqlite_store(monkeypatch: Any, tmp_path: Any) -> None:
    """Test fallback to SQLite peer store when RPC is unavailable."""
    import sqlite3
    
    rpc_url = "http://localhost:9999/rpc"
    monkeypatch.setenv("ANIMICA_RPC_URL", rpc_url)
    
    # Create a test peer store SQLite database
    db_path = tmp_path / "peers.db"
    conn = sqlite3.connect(str(db_path))
    cursor = conn.cursor()
    
    # Create tables
    cursor.execute("""
        CREATE TABLE peers (
            peer_id TEXT PRIMARY KEY,
            address TEXT NOT NULL,
            roles INTEGER,
            chain_id INTEGER,
            alg_policy_root BLOB,
            head_height INTEGER,
            caps TEXT,
            status TEXT,
            first_seen REAL,
            last_seen REAL,
            connected_at REAL,
            last_disconnect REAL,
            rtt_ms REAL,
            score REAL,
            snapshot TEXT
        )
    """)
    cursor.execute("""
        CREATE TABLE peer_addresses (
            peer_id TEXT,
            address TEXT,
            last_seen REAL,
            PRIMARY KEY (peer_id, address)
        )
    """)
    
    # Insert test data
    cursor.execute("""
        INSERT INTO peers VALUES (
            'db_peer1', '/ip4/172.16.0.1/tcp/30303', 0, 0, '', 0, '[]', 'connected',
            1234567890.0, 1234567891.0, 1234567890.0, NULL, 50.0, 20.0, '{}'
        )
    """)
    cursor.execute("""
        INSERT INTO peer_addresses VALUES ('db_peer1', '/ip4/172.16.0.1/tcp/30303', 1234567891.0)
    """)
    cursor.execute("""
        INSERT INTO peer_addresses VALUES ('db_peer1', '/ip4/172.16.0.2/tcp/30304', 1234567892.0)
    """)
    
    conn.commit()
    conn.close()
    
    # Mock all RPC methods to fail
    respx.post(rpc_url).mock(
        return_value=httpx.Response(
            200,
            json={"jsonrpc": "2.0", "id": 1, "error": {"code": -32601, "message": "Method not found"}},
        )
    )
    
    # Note: We pass peers.json path, but _resolve_store_paths() will also check for peers.db
    # in the same directory, which is how it finds our SQLite database
    result = runner.invoke(peer.app, ["list", "--store", str(tmp_path / "peers.json")])
    assert result.exit_code == 0
    assert "Connected Peers: 1" in result.output
    assert "from local peer store" in result.output
    assert "db_peer1" in result.output
