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
