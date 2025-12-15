"""Tests for peer CLI commands."""

from __future__ import annotations

import json
import sqlite3
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
def test_list_peers_rpc_unavailable(monkeypatch: Any, tmp_path: Any) -> None:
    """Test listing peers when RPC is unavailable and no local store exists."""
    rpc_url = "http://localhost:9999/rpc"
    monkeypatch.setenv("ANIMICA_RPC_URL", rpc_url)

    # Mock all potential RPC methods to fail
    respx.post(rpc_url).mock(
        return_value=httpx.Response(
            200,
            json={"jsonrpc": "2.0", "id": 1, "error": {"code": -32601, "message": "Method not found"}},
        )
    )

    # Use a non-existent store path
    nonexistent_store = tmp_path / "nonexistent" / "peers.json"
    result = runner.invoke(peer.app, ["list", "--store", str(nonexistent_store)])
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
def test_add_peer_failure(monkeypatch: Any, tmp_path: Any) -> None:
    """Test adding a peer when RPC fails now falls back to local store."""
    rpc_url = "http://localhost:9999/rpc"
    monkeypatch.setenv("ANIMICA_RPC_URL", rpc_url)

    respx.post(rpc_url).mock(
        return_value=httpx.Response(
            200,
            json={"jsonrpc": "2.0", "id": 1, "error": {"code": -32000, "message": "Failed to add peer"}},
        )
    )

    # With the new fallback logic, this should succeed by writing to store
    store_path = tmp_path / "peers.json"
    result = runner.invoke(peer.app, ["add", "/ip4/1.2.3.4/tcp/30303/p2p/QmPeer1", "--store", str(store_path)])
    assert result.exit_code == 0
    assert "RPC unavailable, but peer saved to local store" in result.output


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
def test_remove_peer_failure(monkeypatch: Any, tmp_path: Any) -> None:
    """Test removing a peer when both RPC and local store fail."""
    rpc_url = "http://localhost:9999/rpc"
    monkeypatch.setenv("ANIMICA_RPC_URL", rpc_url)

    respx.post(rpc_url).mock(
        return_value=httpx.Response(
            200,
            json={"jsonrpc": "2.0", "id": 1, "error": {"code": -32000, "message": "Peer not found"}},
        )
    )

    # Create an empty store so removal will fail both in RPC and store
    store_path = tmp_path / "peers.json"
    store_path.write_text(json.dumps({"peers": []}))

    result = runner.invoke(peer.app, ["remove", "QmPeer1", "--store", str(store_path)])
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


# ==================== Tests for add_peer with store fallback ====================


@respx.mock
def test_add_peer_writes_to_store_on_success(monkeypatch: Any, tmp_path: Any) -> None:
    """Test that add_peer writes to store after successful RPC call."""
    
    rpc_url = "http://localhost:9999/rpc"
    monkeypatch.setenv("ANIMICA_RPC_URL", rpc_url)
    
    store_path = tmp_path / "peers.json"
    
    # Mock RPC to succeed
    respx.post(rpc_url).mock(
        return_value=httpx.Response(
            200,
            json={"jsonrpc": "2.0", "id": 1, "result": True},
        )
    )
    
    result = runner.invoke(peer.app, ["add", "5.6.7.8:30333", "--store", str(store_path)])
    assert result.exit_code == 0
    assert "Successfully added peer" in result.output
    assert "saved to local peer store" in result.output
    
    # Verify peer was written to store
    assert store_path.exists()
    with store_path.open("r") as f:
        data = json.load(f)
    
    peers = data.get("peers", [])
    assert len(peers) == 1
    assert "5.6.7.8:30333" in peers[0]["addrs"]
    assert peers[0]["peer_id"].startswith("peer_")


@respx.mock
def test_add_peer_fallback_to_store_when_rpc_fails(monkeypatch: Any, tmp_path: Any) -> None:
    """Test that add_peer falls back to writing to store when RPC fails."""
    
    rpc_url = "http://localhost:9999/rpc"
    monkeypatch.setenv("ANIMICA_RPC_URL", rpc_url)
    
    store_path = tmp_path / "peers.json"
    
    # Mock all RPC methods to fail
    respx.post(rpc_url).mock(
        return_value=httpx.Response(
            200,
            json={"jsonrpc": "2.0", "id": 1, "error": {"code": -32601, "message": "Method not found"}},
        )
    )
    
    result = runner.invoke(peer.app, ["add", "10.0.0.1:42000", "--store", str(store_path)])
    assert result.exit_code == 0
    assert "RPC unavailable, but peer saved to local store" in result.output
    
    # Verify peer was written to store
    assert store_path.exists()
    with store_path.open("r") as f:
        data = json.load(f)
    
    peers = data.get("peers", [])
    assert len(peers) == 1
    assert "10.0.0.1:42000" in peers[0]["addrs"]


@respx.mock
def test_add_peer_with_multiaddr_extracts_peer_id(monkeypatch: Any, tmp_path: Any) -> None:
    """Test that add_peer extracts peer ID from multiaddr format."""
    
    rpc_url = "http://localhost:9999/rpc"
    monkeypatch.setenv("ANIMICA_RPC_URL", rpc_url)
    
    store_path = tmp_path / "peers.json"
    
    # Mock RPC to fail so we test store-only path
    respx.post(rpc_url).mock(
        return_value=httpx.Response(
            200,
            json={"jsonrpc": "2.0", "id": 1, "error": {"code": -32601, "message": "Method not found"}},
        )
    )
    
    multiaddr = "/ip4/192.168.1.1/tcp/30303/p2p/QmTestPeer123"
    result = runner.invoke(peer.app, ["add", multiaddr, "--store", str(store_path)])
    assert result.exit_code == 0
    
    # Verify peer ID was extracted correctly
    with store_path.open("r") as f:
        data = json.load(f)
    
    peers = data.get("peers", [])
    assert len(peers) == 1
    assert peers[0]["peer_id"] == "QmTestPeer123"
    assert multiaddr in peers[0]["addrs"]


@respx.mock
def test_add_peer_updates_existing_peer(monkeypatch: Any, tmp_path: Any) -> None:
    """Test that add_peer updates an existing peer with new address."""
    
    rpc_url = "http://localhost:9999/rpc"
    monkeypatch.setenv("ANIMICA_RPC_URL", rpc_url)
    
    store_path = tmp_path / "peers.json"
    
    # Pre-populate store with a peer
    existing_data = {
        "peers": [
            {
                "peer_id": "test_peer",
                "addrs": ["/ip4/1.1.1.1/tcp/1111"],
                "score": 5.0,
                "last_seen": 1234567890.0,
                "connected": False,
            }
        ]
    }
    store_path.write_text(json.dumps(existing_data))
    
    # Mock RPC to fail
    respx.post(rpc_url).mock(
        return_value=httpx.Response(
            200,
            json={"jsonrpc": "2.0", "id": 1, "error": {"code": -32601, "message": "Method not found"}},
        )
    )
    
    # Add new address for same peer
    result = runner.invoke(
        peer.app, ["add", "/ip4/1.1.1.1/tcp/2222/p2p/test_peer", "--store", str(store_path)]
    )
    assert result.exit_code == 0
    
    # Verify peer now has both addresses
    with store_path.open("r") as f:
        data = json.load(f)
    
    peers = data.get("peers", [])
    assert len(peers) == 1
    assert peers[0]["peer_id"] == "test_peer"
    assert len(peers[0]["addrs"]) == 2
    assert "/ip4/1.1.1.1/tcp/1111" in peers[0]["addrs"]
    assert "/ip4/1.1.1.1/tcp/2222/p2p/test_peer" in peers[0]["addrs"]


# ==================== Tests for remove_peer with store fallback ====================


@respx.mock
def test_remove_peer_removes_from_store_on_success(monkeypatch: Any, tmp_path: Any) -> None:
    """Test that remove_peer removes from store after successful RPC call."""
    
    rpc_url = "http://localhost:9999/rpc"
    monkeypatch.setenv("ANIMICA_RPC_URL", rpc_url)
    
    store_path = tmp_path / "peers.json"
    
    # Pre-populate store with peers
    existing_data = {
        "peers": [
            {
                "peer_id": "peer_to_remove",
                "addrs": ["/ip4/1.1.1.1/tcp/1111"],
                "score": 0.0,
                "last_seen": 1234567890.0,
                "connected": False,
            },
            {
                "peer_id": "peer_to_keep",
                "addrs": ["/ip4/2.2.2.2/tcp/2222"],
                "score": 0.0,
                "last_seen": 1234567891.0,
                "connected": False,
            }
        ]
    }
    store_path.write_text(json.dumps(existing_data))
    
    # Mock RPC to succeed
    respx.post(rpc_url).mock(
        return_value=httpx.Response(
            200,
            json={"jsonrpc": "2.0", "id": 1, "result": True},
        )
    )
    
    result = runner.invoke(peer.app, ["remove", "peer_to_remove", "--store", str(store_path)])
    assert result.exit_code == 0
    assert "Successfully removed peer" in result.output
    assert "removed from local peer store" in result.output
    
    # Verify peer was removed from store
    with store_path.open("r") as f:
        data = json.load(f)
    
    peers = data.get("peers", [])
    assert len(peers) == 1
    assert peers[0]["peer_id"] == "peer_to_keep"


@respx.mock
def test_remove_peer_fallback_to_store_when_rpc_fails(monkeypatch: Any, tmp_path: Any) -> None:
    """Test that remove_peer falls back to removing from store when RPC fails."""
    
    rpc_url = "http://localhost:9999/rpc"
    monkeypatch.setenv("ANIMICA_RPC_URL", rpc_url)
    
    store_path = tmp_path / "peers.json"
    
    # Pre-populate store with a peer
    existing_data = {
        "peers": [
            {
                "peer_id": "peer_to_remove",
                "addrs": ["/ip4/3.3.3.3/tcp/3333"],
                "score": 0.0,
                "last_seen": 1234567890.0,
                "connected": False,
            }
        ]
    }
    store_path.write_text(json.dumps(existing_data))
    
    # Mock all RPC methods to fail
    respx.post(rpc_url).mock(
        return_value=httpx.Response(
            200,
            json={"jsonrpc": "2.0", "id": 1, "error": {"code": -32601, "message": "Method not found"}},
        )
    )
    
    result = runner.invoke(peer.app, ["remove", "peer_to_remove", "--store", str(store_path)])
    assert result.exit_code == 0
    assert "RPC unavailable, but peer removed from local store" in result.output
    
    # Verify peer was removed from store
    with store_path.open("r") as f:
        data = json.load(f)
    
    peers = data.get("peers", [])
    assert len(peers) == 0


@respx.mock
def test_remove_peer_fails_when_peer_not_found(monkeypatch: Any, tmp_path: Any) -> None:
    """Test that remove_peer fails when peer doesn't exist in RPC or store."""
    
    rpc_url = "http://localhost:9999/rpc"
    monkeypatch.setenv("ANIMICA_RPC_URL", rpc_url)
    
    store_path = tmp_path / "peers.json"
    
    # Create empty store
    store_path.write_text(json.dumps({"peers": []}))
    
    # Mock all RPC methods to fail
    respx.post(rpc_url).mock(
        return_value=httpx.Response(
            200,
            json={"jsonrpc": "2.0", "id": 1, "error": {"code": -32000, "message": "Peer not found"}},
        )
    )
    
    result = runner.invoke(peer.app, ["remove", "nonexistent_peer", "--store", str(store_path)])
    assert result.exit_code == 1
    assert "Failed to remove peer" in result.output


def test_generate_peer_id_from_simple_address() -> None:
    """Test peer ID generation from simple host:port address."""
    from animica.cli.peer import _generate_peer_id
    
    peer_id = _generate_peer_id("192.168.1.1:30303")
    assert peer_id.startswith("peer_")
    assert len(peer_id) == len("peer_") + 32  # peer_ + 32 hex chars (better collision resistance)
    
    # Should be deterministic
    peer_id2 = _generate_peer_id("192.168.1.1:30303")
    assert peer_id == peer_id2


def test_generate_peer_id_extracts_from_multiaddr() -> None:
    """Test peer ID extraction from multiaddr with /p2p/ component."""
    from animica.cli.peer import _generate_peer_id
    
    multiaddr = "/ip4/10.0.0.1/tcp/42000/p2p/QmActualPeerId123"
    peer_id = _generate_peer_id(multiaddr)
    assert peer_id == "QmActualPeerId123"
    
    multiaddr_ipfs = "/ip4/10.0.0.1/tcp/42000/ipfs/IpfsPeerId456"
    peer_id_ipfs = _generate_peer_id(multiaddr_ipfs)
    assert peer_id_ipfs == "IpfsPeerId456"


# ==================== Tests for Issue 1: Better messaging for local store peers ====================


@respx.mock
def test_list_peers_known_peers_message(monkeypatch: Any, tmp_path: Any) -> None:
    """Test that list shows 'Known Peers' when reading from store."""
    
    rpc_url = "http://localhost:9999/rpc"
    monkeypatch.setenv("ANIMICA_RPC_URL", rpc_url)
    
    # Create a test peer store with disconnected peers
    store_path = tmp_path / "peers.json"
    peers_data = {
        "peers": [
            {
                "peer_id": "peer_abc123",
                "addrs": ["5.189.152.183:30333"],
                "score": 0.0,
                "last_seen": 1765836913.9271102,
                "connected": False,
            }
        ]
    }
    store_path.write_text(json.dumps(peers_data))
    
    # Mock RPC to fail
    respx.post(rpc_url).mock(
        return_value=httpx.Response(
            200,
            json={"jsonrpc": "2.0", "id": 1, "error": {"code": -32601, "message": "Method not found"}},
        )
    )
    
    result = runner.invoke(peer.app, ["list", "--store", str(store_path)])
    assert result.exit_code == 0
    assert "Known Peers: 1" in result.output
    assert "from local peer store" in result.output
    assert "peer_abc123" in result.output
    assert "disconnected" in result.output.lower()


@respx.mock
def test_list_peers_no_known_peers_message(monkeypatch: Any, tmp_path: Any) -> None:
    """Test that list shows 'No known peers' when store is empty."""
    
    rpc_url = "http://localhost:9999/rpc"
    monkeypatch.setenv("ANIMICA_RPC_URL", rpc_url)
    
    # Create empty store
    store_path = tmp_path / "peers.json"
    peers_data = {"peers": []}
    store_path.write_text(json.dumps(peers_data))
    
    # Mock RPC to fail
    respx.post(rpc_url).mock(
        return_value=httpx.Response(
            200,
            json={"jsonrpc": "2.0", "id": 1, "error": {"code": -32601, "message": "Method not found"}},
        )
    )
    
    result = runner.invoke(peer.app, ["list", "--store", str(store_path)])
    assert result.exit_code == 0
    assert "No known peers in local peer store" in result.output


# ==================== Tests for Issue 2: Port auto-detection ====================


def test_parse_address_host_port() -> None:
    """Test parsing host:port format."""
    from animica.cli.peer import _parse_address
    
    host, port = _parse_address("192.168.1.1:30303")
    assert host == "192.168.1.1"
    assert port == 30303


def test_parse_address_host_only() -> None:
    """Test parsing host without port."""
    from animica.cli.peer import _parse_address
    
    host, port = _parse_address("144.126.133.21")
    assert host == "144.126.133.21"
    assert port is None


def test_parse_address_multiaddr() -> None:
    """Test parsing multiaddr format."""
    from animica.cli.peer import _parse_address
    
    host, port = _parse_address("/ip4/10.0.0.1/tcp/42000/p2p/QmPeer")
    assert host == "10.0.0.1"
    assert port == 42000


@respx.mock
def test_add_peer_with_port_auto_detection(monkeypatch: Any, tmp_path: Any) -> None:
    """Test that add_peer auto-detects port when not specified."""
    
    rpc_url = "http://localhost:9999/rpc"
    monkeypatch.setenv("ANIMICA_RPC_URL", rpc_url)
    
    store_path = tmp_path / "peers.json"
    
    # Mock RPC to succeed
    respx.post(rpc_url).mock(
        return_value=httpx.Response(
            200,
            json={"jsonrpc": "2.0", "id": 1, "result": True},
        )
    )
    
    # Add peer without port - should default to first port (30333)
    result = runner.invoke(peer.app, ["add", "192.168.1.1", "--store", str(store_path)])
    assert result.exit_code == 0
    assert "Using default port 30333" in result.output
    assert "Successfully added peer" in result.output
    
    # Verify peer was written with port
    with store_path.open("r") as f:
        data = json.load(f)
    peers = data.get("peers", [])
    assert len(peers) == 1
    assert "192.168.1.1:30333" in peers[0]["addrs"]


# ==================== Tests for bootstrap command ====================


@respx.mock
def test_bootstrap_mainnet(monkeypatch: Any, tmp_path: Any) -> None:
    """Test bootstrap command for mainnet."""
    
    rpc_url = "http://localhost:9999/rpc"
    monkeypatch.setenv("ANIMICA_RPC_URL", rpc_url)
    monkeypatch.setenv("ANIMICA_NETWORK", "mainnet")
    
    store_path = tmp_path / "peers.json"
    
    # Mock RPC to succeed
    respx.post(rpc_url).mock(
        return_value=httpx.Response(
            200,
            json={"jsonrpc": "2.0", "id": 1, "result": True},
        )
    )
    
    result = runner.invoke(peer.app, ["bootstrap", "--store", str(store_path)])
    assert result.exit_code == 0
    assert "mainnet seed nodes" in result.output.lower()
    assert "144.126.133.21:30333" in result.output
    assert "Successfully added" in result.output
    
    # Verify seed was written to store
    with store_path.open("r") as f:
        data = json.load(f)
    peers = data.get("peers", [])
    assert len(peers) >= 1
    # Check that at least one peer has the mainnet seed address
    addrs = [addr for p in peers for addr in p.get("addrs", [])]
    assert "144.126.133.21:30333" in addrs


@respx.mock
def test_bootstrap_no_seeds(monkeypatch: Any, tmp_path: Any) -> None:
    """Test bootstrap command when no seeds are configured."""
    
    rpc_url = "http://localhost:9999/rpc"
    monkeypatch.setenv("ANIMICA_RPC_URL", rpc_url)
    
    store_path = tmp_path / "peers.json"
    
    result = runner.invoke(peer.app, ["bootstrap", "--network", "devnet", "--store", str(store_path)])
    assert result.exit_code == 0
    assert "No seed nodes configured" in result.output
