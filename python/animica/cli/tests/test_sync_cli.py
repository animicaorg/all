"""Tests for the sync CLI command."""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from typer.testing import CliRunner

from animica.cli.main import app

runner = CliRunner()


class MockRPCResponse:
    """Mock HTTP response for RPC calls."""
    
    def __init__(self, result: Any = None, error: Optional[Dict[str, Any]] = None):
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
    
    def __init__(self, responses: Dict[str, Any]):
        self.responses = responses
        self.call_count = 0
    
    async def __aenter__(self):
        return self
    
    async def __aexit__(self, *args):
        pass
    
    async def post(self, url: str, json: Dict[str, Any]):
        self.call_count += 1
        method = json.get("method", "")
        
        # Return configured response for this method
        if method in self.responses:
            return MockRPCResponse(result=self.responses[method])
        
        # Default error for unknown methods
        return MockRPCResponse(error={"message": f"Method {method} not found"})


@pytest.fixture
def mock_rpc_success():
    """Mock successful RPC responses."""
    return {
        "chain.getHead": {
            "height": 100,
            "hash": "0x" + "a" * 64,
            "chainId": 1337,
        },
        "node.syncStatus": {
            "syncing": False,
            "synchronized": True,
        },
        "p2p.listPeers": [
            {
                "id": "peer_123",
                "addr": "127.0.0.1:30303",
                "status": "connected",
            },
            {
                "id": "peer_456",
                "addr": "127.0.0.2:30303",
                "status": "connected",
            },
        ],
        "sync.force": {"success": True},
        "sync.start": {"success": True},
    }


@pytest.fixture
def mock_rpc_syncing():
    """Mock RPC responses showing active sync."""
    return {
        "chain.getHead": {
            "height": 50,
            "hash": "0x" + "b" * 64,
            "chainId": 1337,
        },
        "node.syncStatus": {
            "syncing": True,
            "currentBlock": 50,
            "highestBlock": 100,
        },
        "p2p.listPeers": [
            {
                "id": "peer_123",
                "addr": "127.0.0.1:30303",
                "status": "connected",
            },
        ],
    }


@pytest.fixture
def mock_rpc_no_peers():
    """Mock RPC responses with no peers."""
    return {
        "chain.getHead": {
            "height": 10,
            "hash": "0x" + "c" * 64,
            "chainId": 1337,
        },
        "node.syncStatus": {
            "syncing": False,
            "synchronized": True,
        },
        "net.peerCount": 0,
        "net.peers": [],
        "p2p.listPeers": [],
    }


def test_sync_status_success(mock_rpc_success):
    """Test sync status command with successful response."""
    with patch("httpx.AsyncClient") as mock_client:
        mock_client.return_value = MockAsyncClient(mock_rpc_success)
        
        result = runner.invoke(app, ["sync", "status"])
        
        assert result.exit_code == 0
        assert "Blockchain Synchronization Status" in result.stdout
        assert "Height:    100" in result.stdout
        assert "SYNCHRONIZED" in result.stdout
        assert "2 connected" in result.stdout


def test_sync_status_json_output(mock_rpc_success):
    """Test sync status command with JSON output."""
    with patch("httpx.AsyncClient") as mock_client:
        mock_client.return_value = MockAsyncClient(mock_rpc_success)
        
        result = runner.invoke(app, ["sync", "status", "--json"])
        
        assert result.exit_code == 0
        
        # Parse JSON output
        output = json.loads(result.stdout)
        assert output["height"] == 100
        assert output["syncing"] is False
        assert output["peer_count"] == 2
        assert output["chain_id"] == 1337


def test_sync_status_verbose(mock_rpc_success):
    """Test sync status command with verbose output."""
    with patch("httpx.AsyncClient") as mock_client:
        mock_client.return_value = MockAsyncClient(mock_rpc_success)
        
        result = runner.invoke(app, ["sync", "status", "--verbose"])
        
        assert result.exit_code == 0
        assert "Connected Peers:" in result.stdout
        assert "peer_123" in result.stdout
        assert "peer_456" in result.stdout


def test_sync_status_syncing(mock_rpc_syncing):
    """Test sync status when node is actively syncing."""
    with patch("httpx.AsyncClient") as mock_client:
        mock_client.return_value = MockAsyncClient(mock_rpc_syncing)
        
        result = runner.invoke(app, ["sync", "status"])
        
        assert result.exit_code == 0
        assert "SYNCING" in result.stdout
        assert "50 / 100" in result.stdout
        assert "50.0%" in result.stdout


def test_sync_status_no_peers(mock_rpc_no_peers):
    """Test sync status with no connected peers."""
    with patch("httpx.AsyncClient") as mock_client:
        mock_client.return_value = MockAsyncClient(mock_rpc_no_peers)
        
        result = runner.invoke(app, ["sync", "status"])
        
        assert result.exit_code == 0
        assert "0 connected" in result.stdout
        assert "No peers connected" in result.stdout
        assert "animica peer bootstrap" in result.stdout


def test_sync_status_connection_error():
    """Test sync status when unable to connect to node."""
    with patch("httpx.AsyncClient") as mock_client:
        # Simulate connection error - returns empty client that throws on post
        mock_instance = MagicMock()
        
        async def failing_post(*args, **kwargs):
            raise Exception("Connection refused")
        
        mock_instance.post = failing_post
        mock_instance.__aenter__ = AsyncMock(return_value=mock_instance)
        mock_instance.__aexit__ = AsyncMock(return_value=None)
        mock_client.return_value = mock_instance
        
        result = runner.invoke(app, ["sync", "status"])
        
        assert result.exit_code == 1
        output = result.stdout + (result.stderr or "")
        assert "Unable to connect" in output or "RPC unavailable" in output


def test_sync_status_custom_rpc_url(mock_rpc_success):
    """Test sync status with custom RPC URL."""
    with patch("httpx.AsyncClient") as mock_client:
        mock_client.return_value = MockAsyncClient(mock_rpc_success)
        
        result = runner.invoke(
            app,
            ["sync", "status", "--rpc-url", "http://custom:8545/rpc"]
        )
        
        assert result.exit_code == 0
        assert "http://custom:8545/rpc" in result.stdout


def test_sync_force_no_peers(mock_rpc_no_peers):
    """Test force sync with no peers shows warning."""
    with patch("httpx.AsyncClient") as mock_client:
        mock_client.return_value = MockAsyncClient(mock_rpc_no_peers)

        # Simulate user declining to continue
        result = runner.invoke(
            app,
            ["sync", "force"],
            input="n\n"  # Answer "no" to continue prompt
        )

        assert result.exit_code == 0
        assert "No peers connected" in result.stdout
        assert "Cannot sync without peers" in result.stdout


def test_sync_force_auto_bootstrap_and_reseed():
    """Force sync should bootstrap peers and reseed on stalls."""

    responses = {
        "chain.getHead": {"height": 0, "hash": "0x" + "c" * 64},
        "p2p.listPeers": [],
        "sync.force": {"success": True},
    }

    with patch("animica.cli.sync._seed_local_peerstores") as seed_mock:
        seed_mock.return_value = (2, True, [])

        with patch("httpx.AsyncClient") as mock_client:
            mock_client.return_value = MockAsyncClient(responses)

            result = runner.invoke(
                app,
                [
                    "sync",
                    "force",
                    "--timeout",
                    "4",
                    "--check-interval",
                    "1",
                ],
            )

            assert result.exit_code == 0
            assert "Auto-bootstrapping peers from configured seeds" in result.stdout
            # Should reseed at least once more when progress stalls
            assert seed_mock.call_count >= 2


def test_sync_force_success(mock_rpc_success):
    """Test force sync successfully triggers sync."""
    # Create a modified response that shows height increase
    initial_response = mock_rpc_success.copy()
    updated_response = mock_rpc_success.copy()
    updated_response["chain.getHead"] = {
        "height": 105,
        "hash": "0x" + "d" * 64,
        "chainId": 1337,
    }
    
    call_count = [0]
    
    def get_mock_client(*args, **kwargs):
        client = MockAsyncClient(
            initial_response if call_count[0] < 2 else updated_response
        )
        call_count[0] += 1
        return client
    
    with patch("httpx.AsyncClient", side_effect=get_mock_client):
        with patch("time.sleep"):  # Speed up test
            result = runner.invoke(
                app,
                ["sync", "force", "--timeout", "10", "--check-interval", "2"]
            )
            
            assert result.exit_code == 0
            assert "Forcing blockchain synchronization" in result.stdout
            assert "Sync triggered successfully" in result.stdout


def test_sync_force_no_progress(mock_rpc_success):
    """Test force sync when no blocks are synced."""
    with patch("httpx.AsyncClient") as mock_client:
        mock_client.return_value = MockAsyncClient(mock_rpc_success)
        
        with patch("time.sleep"):  # Speed up test
            result = runner.invoke(
                app,
                ["sync", "force", "--timeout", "10", "--check-interval", "2"]
            )
            
            assert result.exit_code == 0
            assert "No blocks synced" in result.stdout
            assert "Node is already at network head" in result.stdout


def test_sync_force_connection_error():
    """Test force sync with connection error."""
    with patch("httpx.AsyncClient") as mock_client:
        # Simulate connection error
        mock_instance = MagicMock()
        
        async def failing_post(*args, **kwargs):
            raise Exception("Connection refused")
        
        mock_instance.post = failing_post
        mock_instance.__aenter__ = AsyncMock(return_value=mock_instance)
        mock_instance.__aexit__ = AsyncMock(return_value=None)
        mock_client.return_value = mock_instance
        
        result = runner.invoke(app, ["sync", "force"])
        
        assert result.exit_code == 1
        assert "Unable to connect to node" in result.stdout


def test_sync_force_with_custom_timeout(mock_rpc_success):
    """Test force sync with custom timeout."""
    with patch("httpx.AsyncClient") as mock_client:
        mock_client.return_value = MockAsyncClient(mock_rpc_success)
        
        with patch("time.sleep"):
            result = runner.invoke(
                app,
                ["sync", "force", "--timeout", "60", "--check-interval", "10"]
            )
            
            assert result.exit_code == 0
            assert "Monitoring sync progress for 60 seconds" in result.stdout
            assert "(Checking every 10 seconds)" in result.stdout


def test_sync_main_help():
    """Test sync main command help."""
    result = runner.invoke(app, ["sync", "--help"])
    
    assert result.exit_code == 0
    assert "Manage blockchain synchronization" in result.stdout
    assert "status" in result.stdout
    assert "force" in result.stdout


def test_sync_status_help():
    """Test sync status subcommand help."""
    result = runner.invoke(app, ["sync", "status", "--help"])
    
    assert result.exit_code == 0
    assert "Show current blockchain synchronization status" in result.stdout
    assert "--json" in result.stdout
    assert "--verbose" in result.stdout


def test_sync_force_help():
    """Test sync force subcommand help."""
    result = runner.invoke(app, ["sync", "force", "--help"])
    
    assert result.exit_code == 0
    assert "Force a blockchain resynchronization" in result.stdout
    assert "--timeout" in result.stdout
    assert "--check-interval" in result.stdout


def test_sync_status_fallback_methods():
    """Test sync status tries fallback RPC methods."""
    # Only provide results for fallback methods
    responses = {
        "chain.getHead": {
            "height": 100,
            "hash": "0x" + "a" * 64,
            "chainId": 1337,
        },
        "chain.syncing": False,  # Fallback sync method
        "admin_peers": [  # Fallback peer method
            {
                "id": "peer_123",
                "addr": "127.0.0.1:30303",
                "status": "connected",
            },
        ],
    }
    
    with patch("httpx.AsyncClient") as mock_client:
        mock_client.return_value = MockAsyncClient(responses)
        
        result = runner.invoke(app, ["sync", "status"])
        
        assert result.exit_code == 0
        assert "Height:    100" in result.stdout


def test_sync_status_no_sync_method_available():
    """Test sync status when no sync RPC methods are available."""
    # Only provide chain.getHead, no sync status methods
    responses = {
        "chain.getHead": {
            "height": 100,
            "hash": "0x" + "a" * 64,
            "chainId": 1337,
        },
        "p2p.listPeers": [],
    }
    
    with patch("httpx.AsyncClient") as mock_client:
        mock_client.return_value = MockAsyncClient(responses)
        
        result = runner.invoke(app, ["sync", "status"])
        
        # Should still work, just without sync status info
        assert result.exit_code == 0
        assert "Height:    100" in result.stdout


def test_sync_force_trigger_fails(mock_rpc_no_peers):
    """Test force sync when trigger RPC methods are not available."""
    with patch("httpx.AsyncClient") as mock_client:
        mock_client.return_value = MockAsyncClient(mock_rpc_no_peers)
        
        # Simulate user choosing to monitor anyway
        with patch("time.sleep"):
            result = runner.invoke(
                app,
                ["sync", "force", "--timeout", "10"],
                input="n\ny\n"  # No to continue without peers, yes to monitor
            )
            
            # Command should handle gracefully
            assert result.exit_code == 0
            assert "Could not trigger sync via RPC" in result.stdout


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
