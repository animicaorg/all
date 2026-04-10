"""Tests for snapshot peer discovery functionality."""

from __future__ import annotations

import asyncio
import json
from typing import Any
from unittest.mock import AsyncMock, patch

import httpx
import pytest
from typer.testing import CliRunner

from animica.cli.main import app
from animica.cli import snapshot as snapshot_cli

runner = CliRunner()


class MockRPCResponse:
    """Mock HTTP response for RPC calls."""
    
    def __init__(self, result: Any = None, error: dict[str, Any] | None = None):
        self.result = result
        self.error = error
    
    def json(self):
        response = {"jsonrpc": "2.0", "id": 1}
        if self.error:
            response["error"] = self.error
        else:
            response["result"] = self.result
        return response


class MockAsyncClient:
    """Mock async HTTP client for testing."""
    
    def __init__(self, responses: dict[str, Any]):
        self.responses = responses
        self.call_count = 0
    
    async def __aenter__(self):
        return self
    
    async def __aexit__(self, *args):
        pass
    
    async def post(self, url: str, json: dict[str, Any], **_kwargs: Any):
        self.call_count += 1
        method = json.get("method", "")
        
        # Check if we have URL-specific responses
        url_key = f"{url}:{method}"
        if url_key in self.responses:
            return MockRPCResponse(result=self.responses[url_key])
        
        # Return configured response for this method
        if method in self.responses:
            return MockRPCResponse(result=self.responses[method])
        
        # Default error for unknown methods
        return MockRPCResponse(error={"message": f"Method {method} not found"})


@pytest.fixture
def mock_rpc_with_peers():
    """Mock RPC responses with connected peers."""
    return {
        "net.peers": [
            {"id": "peer1", "addr": "192.168.1.10:30303"},
            {"id": "peer2", "addr": "192.168.1.11:30303"},
        ],
    }


@pytest.fixture
def mock_peer_snapshots():
    """Mock snapshot responses from different peers."""
    return {
        "http://192.168.1.10:8545/rpc:snapshot.list": {
            "success": True,
            "snapshots": [
                {
                    "chain_id": 1,
                    "checkpoint_height": 1000,
                    "checkpoint_hash": "0xaaa",
                    "blocks_count": 1001,
                    "accounts_count": 50,
                    "size_mb": 10.5,
                },
                {
                    "chain_id": 1,
                    "checkpoint_height": 2000,
                    "checkpoint_hash": "0xbbb",
                    "blocks_count": 2001,
                    "accounts_count": 100,
                    "size_mb": 20.3,
                },
            ],
        },
        "http://192.168.1.11:8545/rpc:snapshot.list": {
            "success": True,
            "snapshots": [
                {
                    "chain_id": 1,
                    "checkpoint_height": 1500,
                    "checkpoint_hash": "0xccc",
                    "blocks_count": 1501,
                    "accounts_count": 75,
                    "size_mb": 15.8,
                },
            ],
        },
    }


def test_get_peers_success(mock_rpc_with_peers):
    """Test getting list of connected peers."""
    with patch("httpx.AsyncClient") as mock_client:
        mock_client.return_value = MockAsyncClient(mock_rpc_with_peers)
        
        peers = asyncio.run(
            snapshot_cli._get_peers("http://127.0.0.1:8545/rpc")
        )
        
        assert len(peers) == 2
        assert peers[0]["id"] == "peer1"
        assert peers[1]["addr"] == "192.168.1.11:30303"


def test_query_peer_snapshots_success():
    """Test querying a single peer for snapshots."""
    responses = {
        "http://192.168.1.10:8545/rpc:snapshot.list": {
            "success": True,
            "snapshots": [
                {
                    "chain_id": 1,
                    "checkpoint_height": 1000,
                    "checkpoint_hash": "0xaaa",
                    "blocks_count": 1001,
                    "accounts_count": 50,
                    "size_mb": 10.5,
                },
            ],
        },
    }
    
    with patch("httpx.AsyncClient") as mock_client:
        mock_client.return_value = MockAsyncClient(responses)
        
        rpc_url, snapshots, error = asyncio.run(
            snapshot_cli._query_peer_snapshots("192.168.1.10:30303", chain_id=1)
        )
        
        assert rpc_url == "http://192.168.1.10:8545/rpc"
        assert len(snapshots) == 1
        assert snapshots[0]["checkpoint_height"] == 1000
        assert snapshots[0]["_source"] == "192.168.1.10:30303"
        assert snapshots[0]["_source_rpc"] == "http://192.168.1.10:8545/rpc"
        assert error is None


def test_query_peer_snapshots_no_snapshots():
    """Test querying a peer with no snapshots."""
    responses = {
        "http://192.168.1.10:8545/rpc:snapshot.list": {
            "success": True,
            "snapshots": [],
        },
    }
    
    with patch("httpx.AsyncClient") as mock_client:
        mock_client.return_value = MockAsyncClient(responses)
        
        rpc_url, snapshots, error = asyncio.run(
            snapshot_cli._query_peer_snapshots("192.168.1.10:30303")
        )
        
        assert snapshots == []
        assert error is None


def test_query_peer_snapshots_error():
    """Test querying a peer that returns an error."""
    responses = {}  # No responses, will cause error
    
    with patch("httpx.AsyncClient") as mock_client:
        mock_client.return_value = MockAsyncClient(responses)
        
        rpc_url, snapshots, error = asyncio.run(
            snapshot_cli._query_peer_snapshots("192.168.1.10:30303")
        )
        
        # Should return empty list and error message on error
        assert snapshots == []
        assert error is not None
        assert "Method snapshot.list not found" in error


def test_query_all_peers_for_snapshots(mock_rpc_with_peers, mock_peer_snapshots):
    """Test querying all connected peers for snapshots."""
    responses = {**mock_rpc_with_peers, **mock_peer_snapshots}
    
    with patch("httpx.AsyncClient") as mock_client:
        mock_client.return_value = MockAsyncClient(responses)
        
        snapshots_by_peer, errors, peer_count = asyncio.run(
            snapshot_cli._query_all_peers_for_snapshots(
                "http://127.0.0.1:8545/rpc", chain_id=1
            )
        )
        
        assert len(snapshots_by_peer) == 2
        assert "http://192.168.1.10:8545/rpc" in snapshots_by_peer
        assert "http://192.168.1.11:8545/rpc" in snapshots_by_peer
        assert len(errors) == 0
        assert peer_count == 2
        
        # Check peer 1 has 2 snapshots
        peer1_snapshots = snapshots_by_peer["http://192.168.1.10:8545/rpc"]
        assert len(peer1_snapshots) == 2
        assert peer1_snapshots[0]["checkpoint_height"] == 1000
        assert peer1_snapshots[1]["checkpoint_height"] == 2000
        
        # Check peer 2 has 1 snapshot
        peer2_snapshots = snapshots_by_peer["http://192.168.1.11:8545/rpc"]
        assert len(peer2_snapshots) == 1
        assert peer2_snapshots[0]["checkpoint_height"] == 1500


def test_query_all_peers_no_peers():
    """Test querying for snapshots when no peers are connected."""
    responses = {
        "net.peers": [],
    }
    
    with patch("httpx.AsyncClient") as mock_client:
        mock_client.return_value = MockAsyncClient(responses)
        
        snapshots_by_peer, errors, peer_count = asyncio.run(
            snapshot_cli._query_all_peers_for_snapshots("http://127.0.0.1:8545/rpc")
        )
        
        assert snapshots_by_peer == {}
        assert len(errors) == 0
        assert peer_count == 0


def test_snapshot_list_from_peers(mock_rpc_with_peers, mock_peer_snapshots):
    """Test snapshot list command with --from-peers flag."""
    responses = {**mock_rpc_with_peers, **mock_peer_snapshots}
    
    with patch("httpx.AsyncClient") as mock_client:
        mock_client.return_value = MockAsyncClient(responses)
        
        result = runner.invoke(app, ["snapshot", "list", "--from-peers"])
        
        assert result.exit_code == 0
        assert "Querying connected peers for snapshots" in result.stdout
        assert "Found 3 snapshot(s) from 2 peer(s)" in result.stdout
        assert "Height 1000" in result.stdout
        assert "Height 1500" in result.stdout
        assert "Height 2000" in result.stdout
        assert "192.168.1.10:30303" in result.stdout


def test_snapshot_list_from_peers_json(mock_rpc_with_peers, mock_peer_snapshots):
    """Test snapshot list command with --from-peers and --json."""
    responses = {**mock_rpc_with_peers, **mock_peer_snapshots}
    
    with patch("httpx.AsyncClient") as mock_client:
        mock_client.return_value = MockAsyncClient(responses)
        
        result = runner.invoke(app, ["snapshot", "list", "--from-peers", "--json"])
        
        assert result.exit_code == 0
        
        # Parse JSON output
        snapshots = json.loads(result.stdout)
        assert len(snapshots) == 3
        assert snapshots[0]["checkpoint_height"] == 2000  # Sorted descending
        assert snapshots[1]["checkpoint_height"] == 1500
        assert snapshots[2]["checkpoint_height"] == 1000


def test_snapshot_list_from_peers_no_snapshots(mock_rpc_with_peers):
    """Test snapshot list when peers have no snapshots."""
    responses = {
        **mock_rpc_with_peers,
        "http://192.168.1.10:8545/rpc:snapshot.list": {
            "success": True,
            "snapshots": [],
        },
        "http://192.168.1.11:8545/rpc:snapshot.list": {
            "success": True,
            "snapshots": [],
        },
    }
    
    with patch("httpx.AsyncClient") as mock_client:
        mock_client.return_value = MockAsyncClient(responses)
        
        result = runner.invoke(app, ["snapshot", "list", "--from-peers"])
        
        assert result.exit_code == 0
        assert "No snapshots found on connected peers" in result.stdout
        assert "💡 Tips:" in result.stdout


def test_snapshot_discover_success(mock_rpc_with_peers, mock_peer_snapshots):
    """Test snapshot discover command."""
    responses = {**mock_rpc_with_peers, **mock_peer_snapshots}
    
    with patch("httpx.AsyncClient") as mock_client:
        mock_client.return_value = MockAsyncClient(responses)
        
        result = runner.invoke(app, ["snapshot", "discover"])
        
        assert result.exit_code == 0
        assert "Discovering snapshots from connected peers" in result.stdout
        assert "Found 3 total snapshot(s) from 2 peer(s)" in result.stdout
        assert "🏆 Best snapshot (highest height):" in result.stdout
        assert "Height:           2000" in result.stdout
        assert "Hash:             0xbbb" in result.stdout
        assert "Source Peer:      192.168.1.10:30303" in result.stdout


def test_snapshot_discover_json(mock_rpc_with_peers, mock_peer_snapshots):
    """Test snapshot discover command with JSON output."""
    responses = {**mock_rpc_with_peers, **mock_peer_snapshots}
    
    with patch("httpx.AsyncClient") as mock_client:
        mock_client.return_value = MockAsyncClient(responses)
        
        result = runner.invoke(app, ["snapshot", "discover", "--json"])
        
        assert result.exit_code == 0
        
        # Parse JSON output
        best_snapshot = json.loads(result.stdout)
        assert best_snapshot["checkpoint_height"] == 2000
        assert best_snapshot["checkpoint_hash"] == "0xbbb"
        assert best_snapshot["_source"] == "192.168.1.10:30303"


def test_snapshot_discover_no_snapshots():
    """Test snapshot discover when no snapshots are available but peers are connected.
    
    This should be treated as informational (exit 0), not an error.
    """
    responses = {
        "snapshot.discoverFromPeers": {
            "success": True,
            "snapshots": [],
            "peer_count": 1,
            "message": "Connected to 1 peer(s), but none have snapshots available.",
        },
    }
    
    with patch("httpx.AsyncClient") as mock_client:
        mock_client.return_value = MockAsyncClient(responses)
        
        result = runner.invoke(app, ["snapshot", "discover"])
        
        # Changed from exit_code == 1 to exit_code == 0 for informational case
        assert result.exit_code == 0
        assert "Connected to" in result.stdout and "peer" in result.stdout
        # Changed from "Troubleshooting" to "Tips" for informational case
        assert "💡 Tips:" in result.stdout


def test_snapshot_list_local_no_snapshots():
    """Test snapshot list when local node has no snapshots."""
    responses = {
        "snapshot.list": {
            "success": True,
            "snapshots": [],
        },
        "net.peers": [],  # No peers connected
    }
    
    with patch("httpx.AsyncClient") as mock_client:
        mock_client.return_value = MockAsyncClient(responses)
        
        result = runner.invoke(app, ["snapshot", "list"])
        
        assert result.exit_code == 0
        assert "No snapshots found on local node" in result.stdout
        assert "💡 Tips:" in result.stdout
        assert "animica snapshot create" in result.stdout


def test_snapshot_list_local_with_auto_peer_discovery(mock_rpc_with_peers, mock_peer_snapshots):
    """Test snapshot list command with automatic peer discovery (default behavior)."""
    responses = {
        "snapshot.list": {
            "success": True,
            "snapshots": [
                {
                    "chain_id": 1,
                    "checkpoint_height": 1000,
                    "checkpoint_hash": "0xaaa",
                    "blocks_count": 1001,
                    "accounts_count": 50,
                    "size_mb": 10.5,
                    "path": "/data/snapshots/chain-1-height-1000",
                }
            ],
        },
        **mock_rpc_with_peers,
        **mock_peer_snapshots,
    }
    
    with patch("httpx.AsyncClient") as mock_client:
        mock_client.return_value = MockAsyncClient(responses)
        
        result = runner.invoke(app, ["snapshot", "list"])
        
        assert result.exit_code == 0
        # Should show local snapshots
        assert "Found 1 local snapshot(s)" in result.stdout
        assert "Height 1000" in result.stdout
        # Should also show highest peer snapshot
        assert "🌐 Highest snapshot from connected peers" in result.stdout
        assert "Height 2000" in result.stdout
        assert "192.168.1.10:30303" in result.stdout
        assert "💡 A higher snapshot is available from peers" in result.stdout


def test_snapshot_list_local_only_flag():
    """Test snapshot list with --local-only flag."""
    responses = {
        "snapshot.list": {
            "success": True,
            "snapshots": [
                {
                    "chain_id": 1,
                    "checkpoint_height": 1000,
                    "checkpoint_hash": "0xaaa",
                    "blocks_count": 1001,
                    "accounts_count": 50,
                    "size_mb": 10.5,
                    "path": "/data/snapshots/chain-1-height-1000",
                }
            ],
        },
    }
    
    with patch("httpx.AsyncClient") as mock_client:
        mock_client.return_value = MockAsyncClient(responses)
        
        result = runner.invoke(app, ["snapshot", "list", "--local-only"])
        
        assert result.exit_code == 0
        # Should show local snapshots
        assert "Found 1 local snapshot(s)" in result.stdout
        # Should NOT query peers
        assert "🌐 Highest snapshot from connected peers" not in result.stdout


def test_snapshot_list_mutually_exclusive_flags():
    """Test that --from-peers and --local-only are mutually exclusive."""
    result = runner.invoke(app, ["snapshot", "list", "--from-peers", "--local-only"])
    
    assert result.exit_code == 1
    assert "mutually exclusive" in result.stdout


def test_snapshot_list_help():
    """Test snapshot list command help."""
    result = runner.invoke(app, ["snapshot", "list", "--help"])
    
    assert result.exit_code == 0
    assert "List all available snapshots" in result.stdout
    assert "--from-peers" in result.stdout
    assert "--local-only" in result.stdout


def test_snapshot_discover_help():
    """Test snapshot discover command help."""
    result = runner.invoke(app, ["snapshot", "discover", "--help"])
    
    assert result.exit_code == 0
    assert "Discover the best available snapshot" in result.stdout
    assert "highest available snapshot" in result.stdout


def test_snapshot_list_with_peer_errors():
    """Test snapshot list command when peer queries fail."""
    responses = {
        "snapshot.list": {
            "success": True,
            "snapshots": [
                {
                    "chain_id": 1,
                    "checkpoint_height": 1000,
                    "checkpoint_hash": "0xaaa",
                    "blocks_count": 1001,
                    "accounts_count": 50,
                    "size_mb": 10.5,
                    "path": "/data/snapshots/chain-1-height-1000",
                }
            ],
        },
        "net.peers": [
            {"id": "peer1", "addr": "192.168.1.10:30303"},
            {"id": "peer2", "addr": "192.168.1.11:30303"},
        ],
        # Peers will fail to respond to snapshot.list
    }
    
    with patch("httpx.AsyncClient") as mock_client:
        mock_client.return_value = MockAsyncClient(responses)
        
        result = runner.invoke(app, ["snapshot", "list"])
        
        assert result.exit_code == 0
        # Should show local snapshots
        assert "Found 1 local snapshot(s)" in result.stdout
        # Should show no peer snapshots found
        assert "💡 No snapshots found on connected peers" in result.stdout
        # Should show error info
        assert "⚠️  Failed to query" in result.stdout
        assert "192.168.1.10:30303" in result.stdout or "192.168.1.11:30303" in result.stdout


def test_snapshot_discover_with_peer_errors():
    """Test snapshot discover command when all peer queries fail."""
    responses = {
        "net.peers": [
            {"id": "peer1", "addr": "192.168.1.10:30303"},
        ],
        # Peer will fail to respond to snapshot.list
    }
    
    with patch("httpx.AsyncClient") as mock_client:
        mock_client.return_value = MockAsyncClient(responses)
        
        result = runner.invoke(app, ["snapshot", "discover"])
        
        assert result.exit_code == 1
        assert "❌ No snapshots found on connected peers" in result.stdout
        # Should show error info
        assert "⚠️  Failed to query" in result.stdout
        assert "192.168.1.10:30303" in result.stdout


def test_snapshot_discover_no_peers_connected():
    """Test snapshot discover command when no peers are connected.
    
    This should be treated as an error (exit 1).
    """
    responses = {
        "snapshot.discoverFromPeers": {
            "success": True,
            "snapshots": [],
            "peer_count": 0,
            "message": "No peers connected. Connect to peers first using 'animica peer add <address>'.",
        },
    }
    
    with patch("httpx.AsyncClient") as mock_client:
        mock_client.return_value = MockAsyncClient(responses)
        
        result = runner.invoke(app, ["snapshot", "discover"])
        
        assert result.exit_code == 1
        assert "No peers" in result.stdout or "peer" in result.stdout.lower()
        assert "💡 Troubleshooting:" in result.stdout
        assert "animica peer" in result.stdout
        # Should NOT show the "no snapshots found" message
        assert "No snapshots found on connected peers" not in result.stdout


def test_snapshot_list_no_peers_connected():
    """Test snapshot list command when no peers are connected."""
    responses = {
        "snapshot.list": {
            "success": True,
            "snapshots": [],
        },
        "net.peers": [],  # No peers connected
    }
    
    with patch("httpx.AsyncClient") as mock_client:
        mock_client.return_value = MockAsyncClient(responses)
        
        result = runner.invoke(app, ["snapshot", "list"])
        
        assert result.exit_code == 0
        assert "❌ No peers connected" in result.stdout
        assert "Connect to peers first" in result.stdout
        # Should NOT show the "no snapshots found on connected peers" message
        assert "No snapshots found on connected peers" not in result.stdout


def test_empty_error_message_handling():
    """Test that empty error messages are handled gracefully."""
    
    class EmptyErrorResponse:
        """Mock response that returns an empty error message."""
        def json(self):
            return {
                "jsonrpc": "2.0",
                "id": 1,
                "error": {"message": ""}  # Empty error message
            }
    
    class MockClientEmptyError:
        """Mock client that returns empty error."""
        async def __aenter__(self):
            return self
        
        async def __aexit__(self, *args):
            pass
        
        async def post(self, url: str, json: dict[str, Any], **kwargs):
            return EmptyErrorResponse()
    
    with patch("httpx.AsyncClient") as mock_client:
        mock_client.return_value = MockClientEmptyError()
        
        # The command should not show an empty error message
        result = runner.invoke(app, ["snapshot", "list"])
        
        # Should fail but with a meaningful error message
        assert result.exit_code == 1
        # Should not have empty error like "Error listing snapshots: "
        # Should have something like "Error listing snapshots: RPC error without message"
        assert "Error listing snapshots:" in result.stderr
        # Make sure the error message isn't just empty after the colon
        lines = [line for line in result.stderr.split('\n') if 'Error listing snapshots:' in line]
        if lines:
            error_line = lines[0]
            # Extract the part after the colon
            after_colon = error_line.split('Error listing snapshots:')[1].strip()
            assert len(after_colon) > 0, "Error message should not be empty"
            assert "RPC error without message" in after_colon or "Unknown error" in after_colon


def test_connection_error_handling():
    """Test that connection errors provide meaningful messages."""
    
    class MockClientConnectionError:
        """Mock client that raises a connection error."""
        async def __aenter__(self):
            return self
        
        async def __aexit__(self, *args):
            pass
        
        async def post(self, url: str, json: dict[str, Any], **kwargs):
            raise httpx.ConnectError("Connection refused")
    
    with patch("httpx.AsyncClient") as mock_client:
        mock_client.return_value = MockClientConnectionError()
        
        result = runner.invoke(app, ["snapshot", "list"])
        
        # Should fail with meaningful error message
        assert result.exit_code == 1
        assert "Error listing snapshots:" in result.stderr
        # Should mention connection failure
        assert "connect" in result.stderr.lower() or "connection" in result.stderr.lower()


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
