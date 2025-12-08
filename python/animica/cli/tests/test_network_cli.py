"""Tests for network CLI commands."""

from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Any

from animica.cli import network
from animica.cli.state import CLIState
from typer.testing import CliRunner

runner = CliRunner()


def test_set_valid_network(monkeypatch: Any) -> None:
    """Test setting a valid network."""
    with tempfile.TemporaryDirectory() as tmpdir:
        state_file = Path(tmpdir) / "state.json"
        monkeypatch.setattr("animica.cli.network.get_cli_state", lambda: CLIState(state_file))

        # Set network to testnet
        result = runner.invoke(network.app, ["set", "testnet"])
        assert result.exit_code == 0
        assert "Active network set to: testnet" in result.output

        # Verify state was persisted
        state = CLIState(state_file)
        assert state.get("active_network") == "testnet"


def test_set_invalid_network() -> None:
    """Test setting an invalid network."""
    result = runner.invoke(network.app, ["set", "invalid-network"])
    assert result.exit_code == 1
    assert "Invalid network" in result.output
    assert "Valid options:" in result.output


def test_get_network_when_set(monkeypatch: Any) -> None:
    """Test getting network when one is set."""
    with tempfile.TemporaryDirectory() as tmpdir:
        state_file = Path(tmpdir) / "state.json"
        state = CLIState(state_file)
        state.set("active_network", "mainnet")
        monkeypatch.setattr("animica.cli.network.get_cli_state", lambda: CLIState(state_file))

        result = runner.invoke(network.app, ["get"])
        assert result.exit_code == 0
        assert "Active network: mainnet" in result.output


def test_get_network_when_not_set(monkeypatch: Any) -> None:
    """Test getting network when none is set."""
    with tempfile.TemporaryDirectory() as tmpdir:
        state_file = Path(tmpdir) / "state.json"
        monkeypatch.setattr("animica.cli.network.get_cli_state", lambda: CLIState(state_file))

        result = runner.invoke(network.app, ["get"])
        assert result.exit_code == 0
        assert "No network has been explicitly set" in result.output
        assert "devnet" in result.output


def test_list_networks() -> None:
    """Test listing available networks."""
    result = runner.invoke(network.app, ["list"])
    assert result.exit_code == 0
    assert "mainnet" in result.output
    assert "testnet" in result.output
    assert "devnet" in result.output
    assert "local-devnet" in result.output


def test_list_networks_shows_active(monkeypatch: Any) -> None:
    """Test that list shows the active network."""
    with tempfile.TemporaryDirectory() as tmpdir:
        state_file = Path(tmpdir) / "state.json"
        state = CLIState(state_file)
        state.set("active_network", "testnet")
        monkeypatch.setattr("animica.cli.network.get_cli_state", lambda: CLIState(state_file))

        result = runner.invoke(network.app, ["list"])
        assert result.exit_code == 0
        assert "Current active network: testnet" in result.output


def test_network_persistence_across_commands(monkeypatch: Any) -> None:
    """Test that network setting persists across multiple commands."""
    with tempfile.TemporaryDirectory() as tmpdir:
        state_file = Path(tmpdir) / "state.json"
        monkeypatch.setattr("animica.cli.network.get_cli_state", lambda: CLIState(state_file))

        # Set network
        result1 = runner.invoke(network.app, ["set", "mainnet"])
        assert result1.exit_code == 0

        # Get network in a new invocation
        result2 = runner.invoke(network.app, ["get"])
        assert result2.exit_code == 0
        assert "mainnet" in result2.output

        # Change network
        result3 = runner.invoke(network.app, ["set", "devnet"])
        assert result3.exit_code == 0

        # Verify it changed
        result4 = runner.invoke(network.app, ["get"])
        assert result4.exit_code == 0
        assert "devnet" in result4.output
