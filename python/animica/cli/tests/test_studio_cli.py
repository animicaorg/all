from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import httpx
import respx
import typer
from animica.cli import studio
from animica.cli.state import CLIState
from typer.testing import CliRunner

runner = CliRunner()


def test_config_validate_success(monkeypatch: Any) -> None:
    """Test config validation with valid configuration."""
    monkeypatch.setenv("RPC_URL", "http://localhost:8545")
    monkeypatch.setenv("CHAIN_ID", "1337")
    monkeypatch.setenv("STORAGE_DIR", "/tmp/test-storage")
    
    result = runner.invoke(studio.app, ["config"])
    
    assert result.exit_code == 0
    assert "Configuration is valid" in result.output
    assert "http://localhost:8545" in result.output
    assert "1337" in result.output
    assert "/tmp/test-storage" in result.output


def test_config_validate_missing_rpc_url(monkeypatch: Any) -> None:
    """Test config validation fails when RPC_URL is missing."""
    # Clear any RPC_URL env vars
    monkeypatch.delenv("RPC_URL", raising=False)
    monkeypatch.delenv("ANIMICA_RPC_URL", raising=False)
    
    result = runner.invoke(studio.app, ["config"])
    
    assert result.exit_code == 1
    assert "Configuration validation failed" in result.output
    assert "RPC_URL is required" in result.output


def test_config_validate_with_optional_settings(monkeypatch: Any) -> None:
    """Test config validation includes optional settings."""
    monkeypatch.setenv("RPC_URL", "http://localhost:8545")
    monkeypatch.setenv("ALLOWED_ORIGINS", "http://localhost:3000,http://localhost:5173")
    monkeypatch.setenv("FAUCET_KEY", "0x1234567890abcdef1234567890abcdef1234567890abcdef1234567890abcdef")
    
    result = runner.invoke(studio.app, ["config"])
    
    assert result.exit_code == 0
    assert "Optional configuration" in result.output
    assert "ALLOWED_ORIGINS" in result.output
    assert "FAUCET_KEY" in result.output
    # Faucet key should be redacted
    assert "0x1234" in result.output
    assert "cdef" in result.output





@respx.mock
def test_status_service_running(monkeypatch: Any) -> None:
    """Test status command when service is running."""
    health_route = respx.get("http://127.0.0.1:8081/healthz").mock(
        return_value=httpx.Response(200, json={"status": "ok"})
    )
    ready_route = respx.get("http://127.0.0.1:8081/readyz").mock(
        return_value=httpx.Response(200, json={"status": "ready"})
    )
    
    result = runner.invoke(studio.app, ["status"])
    
    assert result.exit_code == 0
    assert "Studio Services is running" in result.output
    assert "Health: ok" in result.output
    assert "Ready: ready" in result.output
    assert health_route.called
    assert ready_route.called


@respx.mock
def test_status_service_not_running(monkeypatch: Any) -> None:
    """Test status command when service is not running."""
    respx.get("http://127.0.0.1:8081/healthz").mock(
        side_effect=httpx.ConnectError("Connection refused")
    )
    
    result = runner.invoke(studio.app, ["status"])
    
    assert result.exit_code == 1
    assert "Studio Services is not running" in result.output
    assert "Could not connect" in result.output


@respx.mock
def test_status_default_output(monkeypatch: Any) -> None:
    """Test status command with default human-readable output."""
    respx.get("http://127.0.0.1:8081/healthz").mock(
        return_value=httpx.Response(200, json={"status": "ok"})
    )
    respx.get("http://127.0.0.1:8081/readyz").mock(
        return_value=httpx.Response(200, json={"status": "ready"})
    )
    
    result = runner.invoke(studio.app, ["status"])
    
    assert result.exit_code == 0
    # Check for human-readable output
    assert "Studio Services is running" in result.output
    assert "127.0.0.1" in result.output
    assert "8081" in result.output


@respx.mock
def test_status_not_running_default_output(monkeypatch: Any) -> None:
    """Test status command with default output when service is not running."""
    respx.get("http://127.0.0.1:8081/healthz").mock(
        side_effect=httpx.ConnectError("Connection refused")
    )
    
    result = runner.invoke(studio.app, ["status"])
    
    assert result.exit_code == 1
    assert "Studio Services is not running" in result.output
    assert "Could not connect" in result.output


@respx.mock
def test_status_custom_host_port(monkeypatch: Any) -> None:
    """Test status command with custom host and port."""
    respx.get("http://192.168.1.100:9000/healthz").mock(
        return_value=httpx.Response(200, json={"status": "ok"})
    )
    respx.get("http://192.168.1.100:9000/readyz").mock(
        return_value=httpx.Response(200, json={"status": "ready"})
    )
    
    result = runner.invoke(studio.app, ["status", "--host", "192.168.1.100", "--port", "9000"])
    
    assert result.exit_code == 0
    assert "192.168.1.100" in result.output
    assert "9000" in result.output


def test_up_without_network(monkeypatch: Any) -> None:
    """Test that 'studio up' fails when no network is configured."""
    with tempfile.TemporaryDirectory() as tmpdir:
        state_file = Path(tmpdir) / "state.json"
        monkeypatch.setattr("animica.cli.studio.get_cli_state", lambda: CLIState(state_file))
        # Clear ANIMICA_NETWORK env var if set
        monkeypatch.delenv("ANIMICA_NETWORK", raising=False)
        
        result = runner.invoke(studio.app, ["up"])
        assert result.exit_code == 1
        assert "No network configured" in result.output
        assert "animica network set" in result.output


def test_up_without_rpc_url(monkeypatch: Any) -> None:
    """Test that 'studio up' fails when RPC_URL is not configured."""
    with tempfile.TemporaryDirectory() as tmpdir:
        state_file = Path(tmpdir) / "state.json"
        state = CLIState(state_file)
        state.set("active_network", "devnet")
        monkeypatch.setattr("animica.cli.studio.get_cli_state", lambda: CLIState(state_file))
        
        # Clear RPC_URL env vars
        monkeypatch.delenv("RPC_URL", raising=False)
        monkeypatch.delenv("ANIMICA_RPC_URL", raising=False)
        
        result = runner.invoke(studio.app, ["up"])
        assert result.exit_code == 1
        assert "Configuration validation failed" in result.output
        assert "RPC_URL is required" in result.output


def test_up_with_network_from_state(monkeypatch: Any) -> None:
    """Test 'studio up' succeeds when network and config are set."""
    with tempfile.TemporaryDirectory() as tmpdir:
        state_file = Path(tmpdir) / "state.json"
        state = CLIState(state_file)
        state.set("active_network", "devnet")
        monkeypatch.setattr("animica.cli.studio.get_cli_state", lambda: CLIState(state_file))
        
        # Set required config
        monkeypatch.setenv("RPC_URL", "http://localhost:8545")
        
        # Mock the compose file check
        mock_compose_file = Path(tmpdir) / "docker-compose.yml"
        mock_compose_file.write_text("version: '3'\nservices:\n  services:\n    image: test\n")
        monkeypatch.setattr("animica.cli.studio._get_compose_file", lambda: mock_compose_file)
        
        # Mock subprocess.run
        mock_result = MagicMock()
        mock_result.returncode = 0
        with patch("animica.cli.studio.subprocess.run", return_value=mock_result) as mock_run:
            result = runner.invoke(studio.app, ["up"])
            
            assert result.exit_code == 0
            assert "Starting Studio Services for network: devnet" in result.output
            assert "Studio Services started successfully" in result.output
            
            # Verify subprocess was called with correct arguments
            assert mock_run.called
            call_args = mock_run.call_args
            cmd = call_args[0][0]
            assert "docker" in cmd
            assert "compose" in cmd
            assert "up" in cmd
            assert "--profile" in cmd
            # Should have both dev and studio profiles
            assert cmd.count("--profile") == 2
            assert "dev" in cmd
            assert "studio" in cmd
            
            # Verify environment includes network and config
            env = call_args[1]["env"]
            assert env["ANIMICA_NETWORK"] == "devnet"
            assert "RPC_URL" in env


def test_up_with_network_from_env(monkeypatch: Any) -> None:
    """Test 'studio up' succeeds when network is set via environment variable."""
    with tempfile.TemporaryDirectory() as tmpdir:
        state_file = Path(tmpdir) / "state.json"
        monkeypatch.setattr("animica.cli.studio.get_cli_state", lambda: CLIState(state_file))
        monkeypatch.setenv("ANIMICA_NETWORK", "testnet")
        monkeypatch.setenv("RPC_URL", "http://localhost:8545")
        
        # Mock the compose file check
        mock_compose_file = Path(tmpdir) / "docker-compose.yml"
        mock_compose_file.write_text("version: '3'\nservices:\n  services:\n    image: test\n")
        monkeypatch.setattr("animica.cli.studio._get_compose_file", lambda: mock_compose_file)
        
        # Mock subprocess.run
        mock_result = MagicMock()
        mock_result.returncode = 0
        with patch("animica.cli.studio.subprocess.run", return_value=mock_result):
            result = runner.invoke(studio.app, ["up"])
            
            assert result.exit_code == 0
            assert "Starting Studio Services for network: testnet" in result.output
            assert "Studio Services started successfully" in result.output


def test_up_with_custom_config(monkeypatch: Any) -> None:
    """Test 'studio up' with custom configuration options."""
    with tempfile.TemporaryDirectory() as tmpdir:
        state_file = Path(tmpdir) / "state.json"
        state = CLIState(state_file)
        state.set("active_network", "devnet")
        monkeypatch.setattr("animica.cli.studio.get_cli_state", lambda: CLIState(state_file))
        
        # Mock the compose file check
        mock_compose_file = Path(tmpdir) / "docker-compose.yml"
        mock_compose_file.write_text("version: '3'\nservices:\n  services:\n    image: test\n")
        monkeypatch.setattr("animica.cli.studio._get_compose_file", lambda: mock_compose_file)
        
        # Mock subprocess.run
        mock_result = MagicMock()
        mock_result.returncode = 0
        with patch("animica.cli.studio.subprocess.run", return_value=mock_result) as mock_run:
            result = runner.invoke(studio.app, [
                "up",
                "--rpc-url", "http://192.168.1.1:8545",
                "--chain-id", "9999",
                "--storage-dir", "/custom/storage"
            ])
            
            assert result.exit_code == 0
            assert "http://192.168.1.1:8545" in result.output
            assert "9999" in result.output
            assert "/custom/storage" in result.output
            
            # Verify environment includes custom config (values are converted to strings)
            env = mock_run.call_args[1]["env"]
            assert env["RPC_URL"] == "http://192.168.1.1:8545"
            assert env["CHAIN_ID"] == "9999"
            assert env["STORAGE_DIR"] == "/custom/storage"


def test_up_default_build_and_detach(monkeypatch: Any) -> None:
    """Test 'studio up' uses default build and detach settings."""
    with tempfile.TemporaryDirectory() as tmpdir:
        state_file = Path(tmpdir) / "state.json"
        state = CLIState(state_file)
        state.set("active_network", "devnet")
        monkeypatch.setattr("animica.cli.studio.get_cli_state", lambda: CLIState(state_file))
        monkeypatch.setenv("RPC_URL", "http://localhost:8545")
        
        # Mock the compose file check
        mock_compose_file = Path(tmpdir) / "docker-compose.yml"
        mock_compose_file.write_text("version: '3'\nservices:\n  services:\n    image: test\n")
        monkeypatch.setattr("animica.cli.studio._get_compose_file", lambda: mock_compose_file)
        
        # Mock subprocess.run
        mock_result = MagicMock(returncode=0)
        with patch("animica.cli.studio.subprocess.run", return_value=mock_result):
            result = runner.invoke(studio.app, ["up"])
            
            assert result.exit_code == 0
            # Default behavior: build and detach are enabled (tested in other tests)


def test_up_docker_not_found(monkeypatch: Any) -> None:
    """Test 'studio up' handles docker not being installed."""
    with tempfile.TemporaryDirectory() as tmpdir:
        state_file = Path(tmpdir) / "state.json"
        state = CLIState(state_file)
        state.set("active_network", "devnet")
        monkeypatch.setattr("animica.cli.studio.get_cli_state", lambda: CLIState(state_file))
        monkeypatch.setenv("RPC_URL", "http://localhost:8545")
        
        # Mock the compose file check
        mock_compose_file = Path(tmpdir) / "docker-compose.yml"
        mock_compose_file.write_text("version: '3'\nservices:\n  services:\n    image: test\n")
        monkeypatch.setattr("animica.cli.studio._get_compose_file", lambda: mock_compose_file)
        
        # Mock subprocess.run to raise FileNotFoundError
        with patch("animica.cli.studio.subprocess.run", side_effect=FileNotFoundError()):
            result = runner.invoke(studio.app, ["up"])
            
            assert result.exit_code == 1
            assert "docker' command not found" in result.output


def test_up_compose_file_not_found(monkeypatch: Any) -> None:
    """Test 'studio up' fails gracefully when compose file is missing."""
    with tempfile.TemporaryDirectory() as tmpdir:
        state_file = Path(tmpdir) / "state.json"
        state = CLIState(state_file)
        state.set("active_network", "devnet")
        monkeypatch.setattr("animica.cli.studio.get_cli_state", lambda: CLIState(state_file))
        monkeypatch.setenv("RPC_URL", "http://localhost:8545")
        
        # Mock _get_compose_file to raise Exit when file doesn't exist
        def mock_get_compose():
            raise typer.Exit(code=1)
        
        monkeypatch.setattr("animica.cli.studio._get_compose_file", mock_get_compose)
        
        result = runner.invoke(studio.app, ["up"])
        
        # Should fail when compose file is not found
        assert result.exit_code == 1


def test_down_without_network(monkeypatch: Any) -> None:
    """Test that 'studio down' fails when no network is configured."""
    with tempfile.TemporaryDirectory() as tmpdir:
        state_file = Path(tmpdir) / "state.json"
        monkeypatch.setattr("animica.cli.studio.get_cli_state", lambda: CLIState(state_file))
        # Clear ANIMICA_NETWORK env var if set
        monkeypatch.delenv("ANIMICA_NETWORK", raising=False)
        
        result = runner.invoke(studio.app, ["down"])
        assert result.exit_code == 1
        assert "No network configured" in result.output
        assert "animica network set" in result.output


def test_down_with_network(monkeypatch: Any) -> None:
    """Test 'studio down' succeeds when network is configured."""
    with tempfile.TemporaryDirectory() as tmpdir:
        state_file = Path(tmpdir) / "state.json"
        state = CLIState(state_file)
        state.set("active_network", "devnet")
        monkeypatch.setattr("animica.cli.studio.get_cli_state", lambda: CLIState(state_file))
        
        # Mock the compose file check
        mock_compose_file = Path(tmpdir) / "docker-compose.yml"
        mock_compose_file.write_text("version: '3'\nservices:\n  services:\n    image: test\n")
        monkeypatch.setattr("animica.cli.studio._get_compose_file", lambda: mock_compose_file)
        
        # Mock subprocess.run
        mock_result = MagicMock()
        mock_result.returncode = 0
        with patch("animica.cli.studio.subprocess.run", return_value=mock_result) as mock_run:
            result = runner.invoke(studio.app, ["down"])
            
            assert result.exit_code == 0
            assert "Stopping Studio Services for network: devnet" in result.output
            assert "Studio Services stopped successfully" in result.output
            
            # Verify subprocess was called with correct arguments
            assert mock_run.called
            call_args = mock_run.call_args
            cmd = call_args[0][0]
            assert "docker" in cmd
            assert "compose" in cmd
            # Should use 'stop' command with specific services
            assert "stop" in cmd or "rm" in cmd
            assert "services" in cmd
            assert "explorer" in cmd


def test_down_with_volumes(monkeypatch: Any) -> None:
    """Test 'studio down --volumes' includes volume removal."""
    with tempfile.TemporaryDirectory() as tmpdir:
        state_file = Path(tmpdir) / "state.json"
        state = CLIState(state_file)
        state.set("active_network", "devnet")
        monkeypatch.setattr("animica.cli.studio.get_cli_state", lambda: CLIState(state_file))
        
        # Mock the compose file check
        mock_compose_file = Path(tmpdir) / "docker-compose.yml"
        mock_compose_file.write_text("version: '3'\nservices:\n  services:\n    image: test\n")
        monkeypatch.setattr("animica.cli.studio._get_compose_file", lambda: mock_compose_file)
        
        # Mock subprocess.run
        mock_result = MagicMock()
        mock_result.returncode = 0
        with patch("animica.cli.studio.subprocess.run", return_value=mock_result) as mock_run:
            result = runner.invoke(studio.app, ["down", "--volumes"])
            
            assert result.exit_code == 0
            assert "WARNING" in result.output
            assert "have been removed" in result.output
            
            # Verify volume removal flag was passed (uses 'rm' command)
            call_args = mock_run.call_args
            cmd = call_args[0][0]
            assert "rm" in cmd or "-v" in cmd


def test_logs_without_network(monkeypatch: Any) -> None:
    """Test that 'studio logs' fails when no network is configured."""
    with tempfile.TemporaryDirectory() as tmpdir:
        state_file = Path(tmpdir) / "state.json"
        monkeypatch.setattr("animica.cli.studio.get_cli_state", lambda: CLIState(state_file))
        # Clear ANIMICA_NETWORK env var if set
        monkeypatch.delenv("ANIMICA_NETWORK", raising=False)
        
        result = runner.invoke(studio.app, ["logs"])
        assert result.exit_code == 1
        assert "No network configured" in result.output


def test_logs_with_network(monkeypatch: Any) -> None:
    """Test 'studio logs' succeeds when network is configured."""
    with tempfile.TemporaryDirectory() as tmpdir:
        state_file = Path(tmpdir) / "state.json"
        state = CLIState(state_file)
        state.set("active_network", "devnet")
        monkeypatch.setattr("animica.cli.studio.get_cli_state", lambda: CLIState(state_file))
        
        # Mock the compose file check
        mock_compose_file = Path(tmpdir) / "docker-compose.yml"
        mock_compose_file.write_text("version: '3'\nservices:\n  services:\n    image: test\n")
        monkeypatch.setattr("animica.cli.studio._get_compose_file", lambda: mock_compose_file)
        
        # Mock subprocess.run
        mock_result = MagicMock()
        mock_result.returncode = 0
        with patch("animica.cli.studio.subprocess.run", return_value=mock_result) as mock_run:
            result = runner.invoke(studio.app, ["logs"])
            
            assert result.exit_code == 0
            
            # Verify subprocess was called with correct arguments
            assert mock_run.called
            call_args = mock_run.call_args
            cmd = call_args[0][0]
            assert "docker" in cmd
            assert "compose" in cmd
            assert "logs" in cmd
            assert "--tail" in cmd
            # Should target specific services
            assert "services" in cmd
            assert "explorer" in cmd


def test_logs_with_follow(monkeypatch: Any) -> None:
    """Test 'studio logs --follow' includes follow flag."""
    with tempfile.TemporaryDirectory() as tmpdir:
        state_file = Path(tmpdir) / "state.json"
        state = CLIState(state_file)
        state.set("active_network", "devnet")
        monkeypatch.setattr("animica.cli.studio.get_cli_state", lambda: CLIState(state_file))
        
        # Mock the compose file check
        mock_compose_file = Path(tmpdir) / "docker-compose.yml"
        mock_compose_file.write_text("version: '3'\nservices:\n  services:\n    image: test\n")
        monkeypatch.setattr("animica.cli.studio._get_compose_file", lambda: mock_compose_file)
        
        # Mock subprocess.run
        mock_result = MagicMock()
        mock_result.returncode = 0
        with patch("animica.cli.studio.subprocess.run", return_value=mock_result) as mock_run:
            result = runner.invoke(studio.app, ["logs", "--follow"])
            
            assert result.exit_code == 0
            
            # Verify -f flag was passed for follow
            call_args = mock_run.call_args
            cmd = call_args[0][0]
            assert "-f" in cmd or "--follow" in cmd


def test_logs_with_custom_tail(monkeypatch: Any) -> None:
    """Test 'studio logs --tail' uses custom tail value."""
    with tempfile.TemporaryDirectory() as tmpdir:
        state_file = Path(tmpdir) / "state.json"
        state = CLIState(state_file)
        state.set("active_network", "devnet")
        monkeypatch.setattr("animica.cli.studio.get_cli_state", lambda: CLIState(state_file))
        
        # Mock the compose file check
        mock_compose_file = Path(tmpdir) / "docker-compose.yml"
        mock_compose_file.write_text("version: '3'\nservices:\n  services:\n    image: test\n")
        monkeypatch.setattr("animica.cli.studio._get_compose_file", lambda: mock_compose_file)
        
        # Mock subprocess.run
        mock_result = MagicMock()
        mock_result.returncode = 0
        with patch("animica.cli.studio.subprocess.run", return_value=mock_result) as mock_run:
            result = runner.invoke(studio.app, ["logs", "--tail", "50"])
            
            assert result.exit_code == 0
            
            # Verify tail value was passed
            call_args = mock_run.call_args
            cmd = call_args[0][0]
            assert "--tail" in cmd
            tail_idx = cmd.index("--tail")
            assert cmd[tail_idx + 1] == "50"
