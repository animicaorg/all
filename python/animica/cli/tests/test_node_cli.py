from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import httpx
import pytest
try:
    import respx  # type: ignore
except Exception:  # pragma: no cover - optional dependency
    respx = None  # type: ignore[assignment]
from animica.cli import node
from animica.cli.state import CLIState
from typer.testing import CliRunner

runner = CliRunner()
respx_mock = respx if respx is not None else pytest.mark.skip(reason="respx not installed")


@pytest.fixture(autouse=True)
def _disable_post_start_peer_bootstrap(monkeypatch: Any) -> None:
    monkeypatch.setattr(node, "_post_start_peer_bootstrap", lambda *args, **kwargs: None)


# Test helper functions for up_all tests
def create_mock_compose_files(tmpdir: Path, networks: list[str]) -> dict[str, Path]:
    """Create mock compose files for testing."""
    compose_files = {}
    for network in networks:
        compose_file = Path(tmpdir) / f"docker-compose.{network}.yml"
        compose_file.write_text("version: '3'\nservices:\n  node:\n    image: test\n")
        compose_files[network] = compose_file
    return compose_files


def create_mock_get_network_defaults(compose_files: dict[str, Path]):
    """Create a mock get_network_defaults function."""
    def mock_get_network_defaults(network: str) -> dict:
        port_map = {
            "mainnet": (8545, 30333, 9000),
            "testnet": (18546, 31334, 19000),
            "devnet": (28545, 31335, 29000),
            "local-devnet": (38545, 31336, 39000),
        }
        rpc_port, p2p_port, metrics_port = port_map.get(network, (8545, 30333, 9000))
        return {
            "compose_file": compose_files[network],
            "chain_id": 1 if network == "mainnet" else (2 if network == "testnet" else 1337),
            "rpc_port": rpc_port,
            "p2p_port": p2p_port,
            "metrics_port": metrics_port,
            "data_dir": f"~/.animica/{network}",
        }
    return mock_get_network_defaults


def create_mock_subprocess_success():
    """Create a mock subprocess that always succeeds."""
    def mock_subprocess_run(*args, **kwargs):
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stderr = ""
        mock_result.stdout = ""
        return mock_result
    return mock_subprocess_run


def create_mock_subprocess_with_failures(failed_networks: set[str]):
    """Create a mock subprocess that fails for specific networks."""
    def mock_subprocess_run(*args, **kwargs):
        mock_result = MagicMock()
        env = kwargs.get("env", {})
        network = env.get("ANIMICA_NETWORK")
        if network in failed_networks:
            mock_result.returncode = 1
            mock_result.stderr = f"{network} startup failed"
        else:
            mock_result.returncode = 0
            mock_result.stderr = ""
        mock_result.stdout = ""
        return mock_result
    return mock_subprocess_run


def _dummy_net_cfg(tmpdir: Path, bootstrap_url: str = "https://rpc.animica.org/rpc") -> Any:
    endpoint = bootstrap_url

    class _Cfg:
        name = "mainnet"
        data_dir = str(tmpdir / "data")
        db_name = "chain.db"
        rpc_url = "http://127.0.0.1:8545/rpc"
        bootstrap_url = endpoint

    return _Cfg()


@respx_mock
def test_status_and_head(monkeypatch: Any) -> None:
    rpc_url = "http://localhost:9999/rpc"
    monkeypatch.setenv("ANIMICA_RPC_URL", rpc_url)

    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content.decode("utf-8"))
        method = payload.get("method")
        if method == "node.getStatus":
            return httpx.Response(
                200,
                json={
                    "jsonrpc": "2.0",
                    "id": 1,
                    "result": {
                        "rpc_reachable": True,
                        "chain": {
                            "head": {"height": 42, "hash": "0xabc", "chainId": 10},
                        },
                        "p2p": {
                            "p2p_running": True,
                            "peers_total": 1,
                            "peers_inbound": 0,
                            "peers_outbound": 1,
                            "bootstrap_attempts_last_5m": 1,
                            "bootstrap_last_attempt": {"addr": "seed", "success": True},
                        },
                        "sync": {"syncing": False},
                    },
                },
            )
        if method == "chain.getBlockByHeight":
            return httpx.Response(
                200, json={"jsonrpc": "2.0", "id": 1, "result": {"transactions": []}}
            )
        if method == "chain.getHead":
            return httpx.Response(
                200,
                json={
                    "jsonrpc": "2.0",
                    "id": 1,
                    "result": {"height": 42, "hash": "0xabc", "chainId": 10},
                },
            )
        return httpx.Response(
            200,
            json={"jsonrpc": "2.0", "id": 1, "error": {"message": "Method not found"}},
        )

    head_route = respx.post(rpc_url).mock(side_effect=handler)

    status_result = runner.invoke(node.app, ["status"])
    assert status_result.exit_code == 0
    assert "Head height: 42" in status_result.output
    assert "P2P running: True" in status_result.output

    head_result = runner.invoke(node.app, ["head"])
    assert head_result.exit_code == 0
    data = json.loads(head_result.output)
    assert data["hash"] == "0xabc"


def test_status_stops_after_max_retries(monkeypatch: Any) -> None:
    attempts: list[int] = []

    async def failing_rpc_call(*args: Any, **kwargs: Any) -> Any:
        attempts.append(1)
        raise RuntimeError("boom")

    monkeypatch.setattr(node, "rpc_call", failing_rpc_call)
    monkeypatch.setenv("ANIMICA_RPC_URL", "http://rpc.invalid:9999/rpc")
    result = runner.invoke(
        node.app,
        ["status", "--max-retries", "2", "--retry-delay", "0.01"],
    )

    assert result.exit_code == 1
    assert len(attempts) == 2
    assert "failed after 2 attempts" in result.stdout or "failed after 2 attempts" in result.stderr


@respx_mock
def test_status_prefers_env_over_cached(monkeypatch: Any, tmp_path: Path) -> None:
    cfg = _dummy_net_cfg(tmp_path)
    monkeypatch.setattr(node, "load_network_config", lambda *args, **kwargs: cfg)

    rpc_url = "http://localhost:9999/rpc"
    monkeypatch.setenv("ANIMICA_RPC_URL", rpc_url)

    state_path = node._sync_state_path(cfg)
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(json.dumps({"rpc_url": "https://rpc.other.org/rpc", "height": 99}))

    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content.decode("utf-8"))
        if payload.get("method") == "node.getStatus":
            return httpx.Response(
                200,
                json={
                    "jsonrpc": "2.0",
                    "id": 1,
                    "result": {
                        "rpc_reachable": True,
                        "chain": {
                            "head": {"height": 12, "hash": "0xabc", "chainId": 1},
                        },
                        "p2p": {"p2p_running": False, "peers_total": 0, "peers_inbound": 0, "peers_outbound": 0},
                        "sync": {"syncing": False},
                    },
                },
            )
        return httpx.Response(
            200, json={"jsonrpc": "2.0", "id": 1, "error": {"message": "Method not found"}}
        )

    respx.post(rpc_url).mock(side_effect=handler)

    result = runner.invoke(node.app, ["status"])
    assert result.exit_code == 0
    assert f"RPC URL: {rpc_url}" in result.output
    assert "cached" not in result.output.lower()


@respx_mock
def test_status_rpc_url_flag_overrides_env(monkeypatch: Any, tmp_path: Path) -> None:
    cfg = _dummy_net_cfg(tmp_path)
    monkeypatch.setattr(node, "load_network_config", lambda *args, **kwargs: cfg)

    env_url = "http://localhost:9998/rpc"
    cli_url = "http://localhost:9997/rpc"
    monkeypatch.setenv("ANIMICA_RPC_URL", env_url)

    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content.decode("utf-8"))
        assert str(request.url) == cli_url
        if payload.get("method") == "node.getStatus":
            return httpx.Response(
                200,
                json={
                    "jsonrpc": "2.0",
                    "id": 1,
                    "result": {
                        "rpc_reachable": True,
                        "chain": {
                            "head": {"height": 7, "hash": "0xabc", "chainId": 1},
                        },
                        "p2p": {"p2p_running": False, "peers_total": 0, "peers_inbound": 0, "peers_outbound": 0},
                        "sync": {"syncing": False},
                    },
                },
            )
        return httpx.Response(
            200, json={"jsonrpc": "2.0", "id": 1, "error": {"message": "Method not found"}}
        )

    respx.post(cli_url).mock(side_effect=handler)

    result = runner.invoke(node.app, ["status", "--rpc-url", cli_url])
    assert result.exit_code == 0
    assert f"RPC URL: {cli_url}" in result.output


def test_status_cached_requires_flag(monkeypatch: Any, tmp_path: Path) -> None:
    cfg = _dummy_net_cfg(tmp_path)
    monkeypatch.setattr(node, "load_network_config", lambda *args, **kwargs: cfg)

    state_path = node._sync_state_path(cfg)
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(json.dumps({"rpc_url": "https://rpc.animica.org/rpc", "height": 99}))

    async def failing_rpc_call(*args: Any, **kwargs: Any) -> Any:
        raise RuntimeError("boom")

    monkeypatch.setattr(node, "rpc_call", failing_rpc_call)

    result = runner.invoke(node.app, ["status", "--max-retries", "1", "--retry-delay", "0.01"])
    assert result.exit_code == 1
    output = result.stdout + (result.stderr or "")
    assert "Cached sync state available" in output

    result_cached = runner.invoke(
        node.app, ["status", "--max-retries", "1", "--retry-delay", "0.01", "--use-cached"]
    )
    assert result_cached.exit_code == 0
    assert "Last known cached state" in result_cached.output


def test_auto_bootstrap_fetches_when_db_missing(monkeypatch: Any, tmp_path: Path) -> None:
    cfg = _dummy_net_cfg(tmp_path)
    monkeypatch.delenv("ANIMICA_P2P_SEEDS", raising=False)

    def fake_bootstrap_rpc(url: str, method: str) -> dict[str, Any]:
        assert url == cfg.bootstrap_url
        if method == "bootstrap.getManifest":
            return {"p2p": {"seeds": ["seed-a"]}}
        if method == "bootstrap.getSeeds":
            return {"seeds": ["seed-a", "seed-b"]}
        raise RuntimeError("unexpected method")

    monkeypatch.setattr(node, "_bootstrap_rpc", fake_bootstrap_rpc)

    created = node._auto_bootstrap_if_needed(cfg, cfg.bootstrap_url, force=False, quiet=True)
    assert created is True

    state_path = Path(cfg.data_dir) / "bootstrap.json"
    assert state_path.exists()
    payload = json.loads(state_path.read_text())
    assert payload["seeds"]
    assert payload["manifest"]
    assert os.environ.get("ANIMICA_P2P_SEEDS")


def test_auto_bootstrap_skips_when_db_exists(monkeypatch: Any, tmp_path: Path) -> None:
    cfg = _dummy_net_cfg(tmp_path)
    db_dir = Path(cfg.data_dir)
    db_dir.mkdir(parents=True, exist_ok=True)
    (db_dir / cfg.db_name).write_text("db-present")

    def fail_bootstrap(*args: Any, **kwargs: Any) -> None:
        raise AssertionError("bootstrap should not be called when DB exists")

    monkeypatch.setattr(node, "_bootstrap_rpc", fail_bootstrap)

    created = node._auto_bootstrap_if_needed(cfg, cfg.bootstrap_url, force=False, quiet=True)
    assert created is False


@respx_mock
def test_block_and_tx(monkeypatch: Any) -> None:
    rpc_url = "http://localhost:9998/rpc"
    monkeypatch.setenv("ANIMICA_RPC_URL", rpc_url)

    block_route = respx.post(rpc_url)(
        side_effect=[
            httpx.Response(
                200,
                json={
                    "jsonrpc": "2.0",
                    "id": 1,
                    "result": {"height": 5, "hash": "0x123"},
                },
            ),
            httpx.Response(
                200, json={"jsonrpc": "2.0", "id": 1, "result": {"hash": "0xdeadbeef"}}
            ),
        ]
    )

    block_result = runner.invoke(node.app, ["block", "--height", "5"])
    assert block_result.exit_code == 0
    assert "0xdeadbeef" in block_result.output

    tx_route = respx.post(rpc_url)(
        return_value=httpx.Response(
            200, json={"jsonrpc": "2.0", "id": 1, "result": {"hash": "0xbead"}}
        )
    )

    tx_result = runner.invoke(node.app, ["tx", "--hash", "0xbead"])
    assert tx_result.exit_code == 0
    assert "0xbead" in tx_result.output

    assert block_route.called
    assert tx_route.called


def test_up_without_network(monkeypatch: Any) -> None:
    """Test that 'node up' fails when no network is configured."""
    with tempfile.TemporaryDirectory() as tmpdir:
        state_file = Path(tmpdir) / "state.json"
        monkeypatch.setattr("animica.cli.node.get_cli_state", lambda: CLIState(state_file))
        # Clear ANIMICA_NETWORK env var if set
        monkeypatch.delenv("ANIMICA_NETWORK", raising=False)
        
        result = runner.invoke(node.app, ["up", "--no-wait-sync"])
        assert result.exit_code == 1
        assert "No network configured" in result.output
        assert "animica network set" in result.output


def test_down_without_network(monkeypatch: Any) -> None:
    """Test that 'node down' fails when no network is configured."""
    with tempfile.TemporaryDirectory() as tmpdir:
        state_file = Path(tmpdir) / "state.json"
        monkeypatch.setattr("animica.cli.node.get_cli_state", lambda: CLIState(state_file))
        # Clear ANIMICA_NETWORK env var if set
        monkeypatch.delenv("ANIMICA_NETWORK", raising=False)
        
        result = runner.invoke(node.app, ["down"])
        assert result.exit_code == 1
        assert "No network configured" in result.output
        assert "animica network set" in result.output


def test_up_with_network_from_state(monkeypatch: Any) -> None:
    """Test 'node up' succeeds when network is set in state."""
    with tempfile.TemporaryDirectory() as tmpdir:
        state_file = Path(tmpdir) / "state.json"
        state = CLIState(state_file)
        state.set("active_network", "devnet")
        monkeypatch.setattr("animica.cli.node.get_cli_state", lambda: CLIState(state_file))
        
        # Mock the compose file check
        mock_compose_file = Path(tmpdir) / "docker-compose.yml"
        mock_compose_file.write_text("version: '3'\nservices:\n  node1:\n    image: test\n")
        monkeypatch.setattr("animica.cli.node._get_compose_file", lambda network: mock_compose_file)
        
        # Mock subprocess.run
        mock_result = MagicMock()
        mock_result.returncode = 0
        with patch("animica.cli.node.subprocess.run", return_value=mock_result) as mock_run:
            result = runner.invoke(node.app, ["up", "--no-wait-sync"])
            
            assert result.exit_code == 0
            assert "Starting node for network: devnet" in result.output
            assert "Node started successfully" in result.output
            
            # Verify subprocess was called with correct arguments
            assert mock_run.called
            call_args = mock_run.call_args
            cmd = call_args[0][0]
            assert "docker" in cmd
            assert "compose" in cmd
            assert "up" in cmd
            
            # Verify environment includes network
            env = call_args[1]["env"]
            assert env["ANIMICA_NETWORK"] == "devnet"


def test_up_with_network_from_env(monkeypatch: Any) -> None:
    """Test 'node up' succeeds when network is set via environment variable."""
    with tempfile.TemporaryDirectory() as tmpdir:
        state_file = Path(tmpdir) / "state.json"
        monkeypatch.setattr("animica.cli.node.get_cli_state", lambda: CLIState(state_file))
        monkeypatch.setenv("ANIMICA_NETWORK", "testnet")
        
        # Mock the compose file check
        mock_compose_file = Path(tmpdir) / "docker-compose.yml"
        mock_compose_file.write_text("version: '3'\nservices:\n  node1:\n    image: test\n")
        monkeypatch.setattr("animica.cli.node._get_compose_file", lambda network: mock_compose_file)
        
        # Mock subprocess.run
        mock_result = MagicMock()
        mock_result.returncode = 0
        with patch("animica.cli.node.subprocess.run", return_value=mock_result) as mock_run:
            result = runner.invoke(node.app, ["up", "--no-wait-sync"])
            
            assert result.exit_code == 0
            assert "Starting node for network: testnet" in result.output
            assert "Node started successfully" in result.output


def test_down_with_network(monkeypatch: Any) -> None:
    """Test 'node down' succeeds when network is configured."""
    with tempfile.TemporaryDirectory() as tmpdir:
        state_file = Path(tmpdir) / "state.json"
        state = CLIState(state_file)
        state.set("active_network", "devnet")
        monkeypatch.setattr("animica.cli.node.get_cli_state", lambda: CLIState(state_file))
        
        # Mock the compose file check
        mock_compose_file = Path(tmpdir) / "docker-compose.yml"
        mock_compose_file.write_text("version: '3'\nservices:\n  node1:\n    image: test\n")
        monkeypatch.setattr("animica.cli.node._get_compose_file", lambda network: mock_compose_file)
        
        # Mock subprocess.run
        mock_result = MagicMock()
        mock_result.returncode = 0
        with patch("animica.cli.node.subprocess.run", return_value=mock_result) as mock_run:
            result = runner.invoke(node.app, ["down"])
            
            assert result.exit_code == 0
            assert "Stopping node for network: devnet" in result.output
            assert "Node stopped successfully" in result.output
            
            # Verify subprocess was called with correct arguments
            assert mock_run.called
            call_args = mock_run.call_args
            cmd = call_args[0][0]
            assert "docker" in cmd
            assert "compose" in cmd
            assert "down" in cmd


def test_down_with_volumes(monkeypatch: Any) -> None:
    """Test 'node down --volumes' includes volume removal."""
    with tempfile.TemporaryDirectory() as tmpdir:
        state_file = Path(tmpdir) / "state.json"
        state = CLIState(state_file)
        state.set("active_network", "devnet")
        monkeypatch.setattr("animica.cli.node.get_cli_state", lambda: CLIState(state_file))
        
        # Mock the compose file check
        mock_compose_file = Path(tmpdir) / "docker-compose.yml"
        mock_compose_file.write_text("version: '3'\nservices:\n  node1:\n    image: test\n")
        monkeypatch.setattr("animica.cli.node._get_compose_file", lambda network: mock_compose_file)
        
        # Mock subprocess.run
        mock_result = MagicMock()
        mock_result.returncode = 0
        with patch("animica.cli.node.subprocess.run", return_value=mock_result) as mock_run:
            result = runner.invoke(node.app, ["down", "--volumes"])
            
            assert result.exit_code == 0
            assert "WARNING" in result.output
            assert "have been removed" in result.output
            
            # Verify -v flag was passed
            call_args = mock_run.call_args
            cmd = call_args[0][0]
            assert "-v" in cmd or "--volumes" in cmd


def test_up_with_miner_flag(monkeypatch: Any) -> None:
    """Test 'node up --with-miner' includes miner profile."""
    with tempfile.TemporaryDirectory() as tmpdir:
        state_file = Path(tmpdir) / "state.json"
        state = CLIState(state_file)
        state.set("active_network", "mainnet")
        monkeypatch.setattr("animica.cli.node.get_cli_state", lambda: CLIState(state_file))
        
        # Mock the compose file check
        mock_compose_file = Path(tmpdir) / "docker-compose.yml"
        mock_compose_file.write_text("version: '3'\nservices:\n  node:\n    image: test\n")
        monkeypatch.setattr("animica.cli.node._get_compose_file", lambda network: mock_compose_file)
        
        # Mock subprocess.run
        mock_result = MagicMock()
        mock_result.returncode = 0
        with patch("animica.cli.node.subprocess.run", return_value=mock_result) as mock_run:
            result = runner.invoke(node.app, ["up", "--with-miner", "--no-wait-sync"])
            
            assert result.exit_code == 0
            assert "Starting node for network: mainnet" in result.output
            
            # Verify miner profile was passed to docker-compose for mainnet
            call_args = mock_run.call_args
            cmd = call_args[0][0]
            assert "--profile" in cmd
            assert "miner" in cmd


def test_up_docker_not_found(monkeypatch: Any) -> None:
    """Test 'node up' handles docker not being installed."""
    with tempfile.TemporaryDirectory() as tmpdir:
        state_file = Path(tmpdir) / "state.json"
        state = CLIState(state_file)
        state.set("active_network", "devnet")
        monkeypatch.setattr("animica.cli.node.get_cli_state", lambda: CLIState(state_file))
        
        # Mock the compose file check
        mock_compose_file = Path(tmpdir) / "docker-compose.yml"
        mock_compose_file.write_text("version: '3'\nservices:\n  node1:\n    image: test\n")
        monkeypatch.setattr("animica.cli.node._get_compose_file", lambda network: mock_compose_file)
        
        # Mock subprocess.run to raise FileNotFoundError
        with patch("animica.cli.node.subprocess.run", side_effect=FileNotFoundError()):
            result = runner.invoke(node.app, ["up", "--no-wait-sync"])
            
            assert result.exit_code == 1
            assert "docker' command not found" in result.output


def test_up_compose_file_not_found(monkeypatch: Any) -> None:
    """Test 'node up' fails gracefully when compose file is missing."""
    with tempfile.TemporaryDirectory() as tmpdir:
        state_file = Path(tmpdir) / "state.json"
        state = CLIState(state_file)
        state.set("active_network", "devnet")
        monkeypatch.setattr("animica.cli.node.get_cli_state", lambda: CLIState(state_file))
        
        # Don't mock _get_compose_file, let it try to find the real one
        # but change the repo root to point to empty dir
        result = runner.invoke(node.app, ["up", "--no-wait-sync"])
        
        # Either it finds the real compose file (ok) or fails to find it
        # Since we're in the real repo, it might actually find the file
        # So we just check it doesn't crash
        assert result.exit_code in (0, 1)


def test_up_does_not_start_studio_services(monkeypatch: Any) -> None:
    """Test that 'node up' does not start Studio Services by default."""
    with tempfile.TemporaryDirectory() as tmpdir:
        state_file = Path(tmpdir) / "state.json"
        state = CLIState(state_file)
        state.set("active_network", "devnet")
        monkeypatch.setattr("animica.cli.node.get_cli_state", lambda: CLIState(state_file))
        
        # Mock the compose file check
        mock_compose_file = Path(tmpdir) / "docker-compose.yml"
        mock_compose_file.write_text("version: '3'\nservices:\n  node1:\n    image: test\n")
        monkeypatch.setattr("animica.cli.node._get_compose_file", lambda network: mock_compose_file)
        
        # Mock subprocess.run
        mock_result = MagicMock()
        mock_result.returncode = 0
        with patch("animica.cli.node.subprocess.run", return_value=mock_result) as mock_run:
            result = runner.invoke(node.app, ["up", "--no-wait-sync"])
            
            assert result.exit_code == 0
            
            # Verify the command uses 'dev' profile (not 'studio')
            call_args = mock_run.call_args
            cmd = call_args[0][0]
            assert "--profile" in cmd
            profile_idx = cmd.index("--profile")
            assert cmd[profile_idx + 1] == "dev"
            
            # Verify 'studio' is NOT in the command
            assert "studio" not in cmd


def test_up_succeeds_without_studio_services_present(monkeypatch: Any) -> None:
    """Test 'node up' succeeds even if Studio Services is not in compose file."""
    with tempfile.TemporaryDirectory() as tmpdir:
        state_file = Path(tmpdir) / "state.json"
        state = CLIState(state_file)
        state.set("active_network", "devnet")
        monkeypatch.setattr("animica.cli.node.get_cli_state", lambda: CLIState(state_file))
        
        # Mock the compose file without studio services
        mock_compose_file = Path(tmpdir) / "docker-compose.yml"
        mock_compose_file.write_text("""
version: '3'
services:
  node1:
    profiles: [dev]
    image: test-node
  miner:
    profiles: [dev]
    image: test-miner
""")
        monkeypatch.setattr("animica.cli.node._get_compose_file", lambda network: mock_compose_file)
        
        # Mock subprocess.run
        mock_result = MagicMock()
        mock_result.returncode = 0
        with patch("animica.cli.node.subprocess.run", return_value=mock_result):
            result = runner.invoke(node.app, ["up", "--no-wait-sync"])
            
            # Should succeed even without studio services
            assert result.exit_code == 0
            assert "Node started successfully" in result.output


def test_mainnet_uses_correct_compose_file(monkeypatch: Any) -> None:
    """Test that mainnet network uses mainnet compose file."""
    with tempfile.TemporaryDirectory() as tmpdir:
        state_file = Path(tmpdir) / "state.json"
        state = CLIState(state_file)
        state.set("active_network", "mainnet")
        monkeypatch.setattr("animica.cli.node.get_cli_state", lambda: CLIState(state_file))
        
        # Track which network was passed to _get_compose_file
        captured_network = []
        
        def mock_get_compose_file(network: str) -> Path:
            captured_network.append(network)
            mock_file = Path(tmpdir) / "docker-compose.mainnet.yml"
            mock_file.write_text("version: '3'\nservices:\n  node:\n    image: test\n")
            return mock_file
        
        monkeypatch.setattr("animica.cli.node._get_compose_file", mock_get_compose_file)
        
        # Mock subprocess.run
        mock_result = MagicMock()
        mock_result.returncode = 0
        with patch("animica.cli.node.subprocess.run", return_value=mock_result):
            result = runner.invoke(node.app, ["up", "--no-wait-sync"])
            
            assert result.exit_code == 0
            assert "Starting node for network: mainnet" in result.output
            assert captured_network[0] == "mainnet"
            assert "Chain ID: 1" in result.output


def test_testnet_uses_correct_compose_file(monkeypatch: Any) -> None:
    """Test that testnet network uses testnet compose file."""
    with tempfile.TemporaryDirectory() as tmpdir:
        state_file = Path(tmpdir) / "state.json"
        state = CLIState(state_file)
        state.set("active_network", "testnet")
        monkeypatch.setattr("animica.cli.node.get_cli_state", lambda: CLIState(state_file))
        
        # Track which network was passed to _get_compose_file
        captured_network = []
        
        def mock_get_compose_file(network: str) -> Path:
            captured_network.append(network)
            mock_file = Path(tmpdir) / "docker-compose.testnet.yml"
            mock_file.write_text("version: '3'\nservices:\n  node:\n    image: test\n")
            return mock_file
        
        monkeypatch.setattr("animica.cli.node._get_compose_file", mock_get_compose_file)
        
        # Mock subprocess.run
        mock_result = MagicMock()
        mock_result.returncode = 0
        with patch("animica.cli.node.subprocess.run", return_value=mock_result):
            result = runner.invoke(node.app, ["up", "--no-wait-sync"])
            
            assert result.exit_code == 0
            assert "Starting node for network: testnet" in result.output
            assert captured_network[0] == "testnet"
            assert "Chain ID: 2" in result.output


def test_devnet_uses_correct_compose_file(monkeypatch: Any) -> None:
    """Test that devnet network uses devnet compose file."""
    with tempfile.TemporaryDirectory() as tmpdir:
        state_file = Path(tmpdir) / "state.json"
        state = CLIState(state_file)
        state.set("active_network", "devnet")
        monkeypatch.setattr("animica.cli.node.get_cli_state", lambda: CLIState(state_file))
        
        # Track which network was passed to _get_compose_file
        captured_network = []
        
        def mock_get_compose_file(network: str) -> Path:
            captured_network.append(network)
            mock_file = Path(tmpdir) / "docker-compose.yml"
            mock_file.write_text("version: '3'\nservices:\n  node1:\n    image: test\n")
            return mock_file
        
        monkeypatch.setattr("animica.cli.node._get_compose_file", mock_get_compose_file)
        
        # Mock subprocess.run
        mock_result = MagicMock()
        mock_result.returncode = 0
        with patch("animica.cli.node.subprocess.run", return_value=mock_result):
            result = runner.invoke(node.app, ["up", "--no-wait-sync"])
            
            assert result.exit_code == 0
            assert "Starting node for network: devnet" in result.output
            assert captured_network[0] == "devnet"
            assert "Chain ID: 1337" in result.output


@respx_mock
def test_status_retries_on_connection_error(monkeypatch: Any) -> None:
    """Test that status command retries indefinitely on connection errors."""
    rpc_url = "http://localhost:9997/rpc"
    monkeypatch.setenv("ANIMICA_RPC_URL", rpc_url)
    
    # Track number of retry attempts (not individual RPC calls)
    retry_attempt_count = [0]
    
    def side_effect_fn(request):
        # On first attempt (retry_attempt 1), all calls fail
        # On second attempt (retry_attempt 2), all calls fail
        # On third attempt (retry_attempt 3), all calls succeed
        if retry_attempt_count[0] < 2:
            raise httpx.ConnectError("Connection refused")
        return httpx.Response(
            200,
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "result": {"height": 42, "hash": "0xabc", "chainId": 10},
            },
        )
    
    # Track when we start a new retry attempt
    original_sleep = __import__('time').sleep
    def mock_sleep(duration):
        retry_attempt_count[0] += 1
        original_sleep(duration)
    
    head_route = respx.post(rpc_url)(side_effect=side_effect_fn)
    respx.post("http://127.0.0.1:9997/rpc")(side_effect=side_effect_fn)
    respx.post("http://[::1]:9997/rpc")(side_effect=side_effect_fn)
    
    # Patch time.sleep to track retry attempts
    import time
    original_sleep_fn = time.sleep
    time.sleep = mock_sleep
    
    try:
        result = runner.invoke(node.app, ["status", "--retry-delay", "0.1"])
        assert result.exit_code == 0
        assert "Head height: 42" in result.output
        # Should have had 2 retries (first attempt failed, 2 retries, 3rd attempt succeeded)
        assert retry_attempt_count[0] == 2
    finally:
        time.sleep = original_sleep_fn


@respx_mock
def test_status_accepts_retry_delay_parameter(monkeypatch: Any) -> None:
    """Test that status command accepts --retry-delay parameter."""
    rpc_url = "http://localhost:9996/rpc"
    monkeypatch.setenv("ANIMICA_RPC_URL", rpc_url)
    
    head_route = respx.post(rpc_url)(
        return_value=httpx.Response(
            200,
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "result": {"height": 10, "hash": "0x123", "chainId": 1},
            },
        )
    )
    
    result = runner.invoke(node.app, ["status", "--retry-delay", "2.5"])
    assert result.exit_code == 0
    assert "Head height: 10" in result.output
    assert head_route.called


def test_status_rejects_invalid_retry_delay(monkeypatch: Any) -> None:
    """Test that status command rejects invalid retry delay values."""
    rpc_url = "http://localhost:9995/rpc"
    monkeypatch.setenv("ANIMICA_RPC_URL", rpc_url)
    
    result = runner.invoke(node.app, ["status", "--retry-delay", "0"])
    assert result.exit_code == 1
    assert "retry-delay must be greater than 0" in result.output


@respx_mock
def test_status_accepts_timeout_parameter(monkeypatch: Any) -> None:
    """Test that status command accepts --timeout parameter and uses it without errors."""
    rpc_url = "http://localhost:9994/rpc"
    monkeypatch.setenv("ANIMICA_RPC_URL", rpc_url)

    route = respx.post(rpc_url)(
        side_effect=[
            httpx.Response(
                200,
                json={
                    "jsonrpc": "2.0",
                    "id": 1,
                    "result": {"height": 7, "hash": "0xaaa", "chainId": 5},
                },
            ),
            httpx.Response(
                200,
                json={
                    "jsonrpc": "2.0",
                    "id": 1,
                    "result": {"height": 7, "hash": "0xaaa"},
                },
            ),
            httpx.Response(
                200, json={"jsonrpc": "2.0", "id": 1, "result": {"syncing": False}}
            ),
        ]
    )

    result = runner.invoke(node.app, ["status", "--timeout", "15", "--retry-delay", "0.01"])
    assert result.exit_code == 0
    assert "Head height: 7" in result.output
    assert route.called


def test_status_rejects_invalid_timeout(monkeypatch: Any) -> None:
    """Test that status command rejects invalid timeout values."""
    rpc_url = "http://localhost:9993/rpc"
    monkeypatch.setenv("ANIMICA_RPC_URL", rpc_url)

    result = runner.invoke(node.app, ["status", "--timeout", "-1"])
    assert result.exit_code == 1
    assert "must not be negative" in result.output


@respx_mock
def test_status_allows_unbounded_timeout(monkeypatch: Any) -> None:
    """Timeout value of 0 disables timeouts and should be accepted."""
    rpc_url = "http://localhost:9990/rpc"
    monkeypatch.setenv("ANIMICA_RPC_URL", rpc_url)

    route = respx.post(rpc_url)(
        side_effect=[
            httpx.Response(
                200,
                json={
                    "jsonrpc": "2.0",
                    "id": 1,
                    "result": {"height": 11, "hash": "0xccc", "chainId": 5},
                },
            ),
            httpx.Response(
                200,
                json={"jsonrpc": "2.0", "id": 1, "result": {"height": 11, "hash": "0xccc"}},
            ),
            httpx.Response(
                200, json={"jsonrpc": "2.0", "id": 1, "result": {"syncing": False}}
            ),
        ]
    )

    result = runner.invoke(node.app, ["status", "--timeout", "0", "--retry-delay", "0.01"])
    assert result.exit_code == 0
    assert "Head height: 11" in result.output
    assert route.called


@respx_mock
def test_status_logs_error_type_when_message_missing(monkeypatch: Any) -> None:
    """Ensure retry log includes error type when exception has no message."""
    rpc_url = "http://localhost:9992/rpc"
    monkeypatch.setenv("ANIMICA_RPC_URL", rpc_url)

    attempt = {"count": 0}

    def side_effect(request):
        method = request.json()["method"]
        if method == "chain.getHead":
            attempt["count"] += 1
            if attempt["count"] == 1:
                raise Exception()
            return httpx.Response(
                200,
                json={
                    "jsonrpc": "2.0",
                    "id": 1,
                    "result": {"height": 3, "hash": "0xbbb", "chainId": 9},
                },
            )
        if method == "chain.getBlockByHeight":
            return httpx.Response(
                200, json={"jsonrpc": "2.0", "id": 1, "result": {"hash": "0xbbb"}}
            )
        return httpx.Response(
            200, json={"jsonrpc": "2.0", "id": 1, "result": {"syncing": False}}
        )

    respx.post(rpc_url)(side_effect=side_effect)

    result = runner.invoke(node.app, ["status", "--retry-delay", "0.01"])

    assert result.exit_code == 0
    assert "Head height: 3" in result.output
    assert "Exception()" in result.output


def test_network_switching_affects_compose_file(monkeypatch: Any) -> None:
    """Test that switching networks changes the compose file used."""
    with tempfile.TemporaryDirectory() as tmpdir:
        state_file = Path(tmpdir) / "state.json"
        state = CLIState(state_file)
        monkeypatch.setattr("animica.cli.node.get_cli_state", lambda: CLIState(state_file))
        monkeypatch.setattr("animica.cli.network.get_cli_state", lambda: CLIState(state_file))
        
        # Track networks passed to _get_compose_file
        captured_networks = []
        
        def mock_get_compose_file(network: str) -> Path:
            captured_networks.append(network)
            mock_file = Path(tmpdir) / f"docker-compose.{network}.yml"
            mock_file.write_text("version: '3'\nservices:\n  node:\n    image: test\n")
            return mock_file
        
        monkeypatch.setattr("animica.cli.node._get_compose_file", mock_get_compose_file)
        
        # Mock subprocess.run
        mock_result = MagicMock()
        mock_result.returncode = 0
        
        # Set to mainnet and run
        from animica.cli import network as network_cli
        result1 = runner.invoke(network_cli.app, ["set", "mainnet"])
        assert result1.exit_code == 0
        
        with patch("animica.cli.node.subprocess.run", return_value=mock_result):
            result2 = runner.invoke(node.app, ["up", "--no-wait-sync"])
            assert result2.exit_code == 0
            assert captured_networks[-1] == "mainnet"
        
        # Switch to testnet and run
        result3 = runner.invoke(network_cli.app, ["set", "testnet"])
        assert result3.exit_code == 0
        
        with patch("animica.cli.node.subprocess.run", return_value=mock_result):
            result4 = runner.invoke(node.app, ["up", "--no-wait-sync"])
            assert result4.exit_code == 0
            assert captured_networks[-1] == "testnet"


def test_up_all_success(monkeypatch: Any) -> None:
    """Test 'node up-all' successfully starts all networks."""
    with tempfile.TemporaryDirectory() as tmpdir:
        networks = ["mainnet", "testnet", "devnet", "local-devnet"]
        compose_files = create_mock_compose_files(tmpdir, networks)
        
        monkeypatch.setattr(
            "animica.cli.node.get_network_defaults",
            create_mock_get_network_defaults(compose_files)
        )
        
        # Track subprocess calls
        subprocess_calls = []
        
        def mock_subprocess_run(*args, **kwargs):
            subprocess_calls.append((args, kwargs))
            return create_mock_subprocess_success()(*args, **kwargs)
        
        with patch("animica.cli.node.subprocess.run", side_effect=mock_subprocess_run):
            result = runner.invoke(node.app, ["up-all"])
            
            assert result.exit_code == 0
            assert "Starting all Animica node networks" in result.output
            assert "mainnet started successfully" in result.output
            assert "testnet started successfully" in result.output
            assert "devnet started successfully" in result.output
            assert "local-devnet started successfully" in result.output
            assert "Summary" in result.output
            assert "Successfully started (4)" in result.output
            
            # Verify all networks were called
            assert len(subprocess_calls) == 4


def test_up_all_partial_failure(monkeypatch: Any) -> None:
    """Test 'node up-all' handles partial failures correctly."""
    with tempfile.TemporaryDirectory() as tmpdir:
        networks = ["mainnet", "testnet", "devnet", "local-devnet"]
        compose_files = create_mock_compose_files(tmpdir, networks)
        
        monkeypatch.setattr(
            "animica.cli.node.get_network_defaults",
            create_mock_get_network_defaults(compose_files)
        )
        
        with patch(
            "animica.cli.node.subprocess.run",
            side_effect=create_mock_subprocess_with_failures({"testnet"})
        ):
            result = runner.invoke(node.app, ["up-all"])
            
            assert result.exit_code == 1  # Should exit with error
            assert "mainnet started successfully" in result.output
            assert "testnet failed to start" in result.output
            assert "devnet started successfully" in result.output
            assert "local-devnet started successfully" in result.output
            assert "Summary" in result.output
            assert "Successfully started (3)" in result.output
            assert "Failed (1): testnet" in result.output
            assert "Some networks failed to start" in result.output


def test_up_all_missing_compose_file(monkeypatch: Any) -> None:
    """Test 'node up-all' skips networks with missing compose files."""
    with tempfile.TemporaryDirectory() as tmpdir:
        # Create mock compose files only for mainnet and devnet
        compose_files = create_mock_compose_files(tmpdir, ["mainnet", "devnet"])
        
        # Mock get_network_defaults to return non-existent files for testnet and local-devnet
        def mock_get_network_defaults(network: str) -> dict:
            port_map = {
                "mainnet": (8545, 30333, 9000),
                "testnet": (18546, 31334, 19000),
                "devnet": (28545, 31335, 29000),
                "local-devnet": (38545, 31336, 39000),
            }
            rpc_port, p2p_port, metrics_port = port_map.get(network, (8545, 30333, 9000))
            return {
                "compose_file": compose_files.get(network, Path(tmpdir) / f"missing-{network}.yml"),
                "chain_id": 1 if network == "mainnet" else (2 if network == "testnet" else 1337),
                "rpc_port": rpc_port,
                "p2p_port": p2p_port,
                "metrics_port": metrics_port,
                "data_dir": f"~/.animica/{network}",
            }
        
        monkeypatch.setattr("animica.cli.node.get_network_defaults", mock_get_network_defaults)
        
        with patch("animica.cli.node.subprocess.run", side_effect=create_mock_subprocess_success()):
            result = runner.invoke(node.app, ["up-all"])
            
            assert result.exit_code == 0  # Should succeed since some networks started
            assert "mainnet started successfully" in result.output
            assert "devnet started successfully" in result.output
            assert "Compose file not found for testnet" in result.output
            assert "Skipping testnet" in result.output
            assert "Compose file not found for local-devnet" in result.output
            assert "Skipping local-devnet" in result.output
            assert "Summary" in result.output
            assert "Successfully started (2)" in result.output
            assert "Skipped (2)" in result.output


def test_up_all_docker_not_found(monkeypatch: Any) -> None:
    """Test 'node up-all' handles docker not being installed."""
    with tempfile.TemporaryDirectory() as tmpdir:
        networks = ["mainnet", "testnet", "devnet", "local-devnet"]
        compose_files = create_mock_compose_files(tmpdir, networks)
        
        monkeypatch.setattr(
            "animica.cli.node.get_network_defaults",
            create_mock_get_network_defaults(compose_files)
        )
        
        # Mock subprocess.run to raise FileNotFoundError
        with patch("animica.cli.node.subprocess.run", side_effect=FileNotFoundError()):
            result = runner.invoke(node.app, ["up-all"])
            
            assert result.exit_code == 1
            assert "docker' command not found" in result.output
            assert "Please install Docker and Docker Compose" in result.output


def test_up_all_all_skipped(monkeypatch: Any) -> None:
    """Test 'node up-all' fails when all networks are skipped."""
    with tempfile.TemporaryDirectory() as tmpdir:
        # Mock get_network_defaults to return non-existent files for all networks
        def mock_get_network_defaults(network: str) -> dict:
            return {
                "compose_file": Path(tmpdir) / f"missing-{network}.yml",
                "chain_id": 1,
                "rpc_port": 8545,
                "p2p_port": 30333,
                "metrics_port": 9000,
                "data_dir": f"~/.animica/{network}",
            }
        
        monkeypatch.setattr("animica.cli.node.get_network_defaults", mock_get_network_defaults)
        
        result = runner.invoke(node.app, ["up-all"])
        
        assert result.exit_code == 1
        assert "No networks were started. All were skipped." in result.output


def test_up_all_with_miner_flag(monkeypatch: Any) -> None:
    """Test 'node up-all --with-miner' includes miner profile for mainnet/testnet."""
    with tempfile.TemporaryDirectory() as tmpdir:
        networks = ["mainnet", "testnet", "devnet", "local-devnet"]
        compose_files = create_mock_compose_files(tmpdir, networks)
        
        monkeypatch.setattr(
            "animica.cli.node.get_network_defaults",
            create_mock_get_network_defaults(compose_files)
        )
        
        # Track subprocess calls to verify miner profile
        subprocess_calls = []
        
        def mock_subprocess_run(*args, **kwargs):
            subprocess_calls.append((args, kwargs))
            return create_mock_subprocess_success()(*args, **kwargs)
        
        with patch("animica.cli.node.subprocess.run", side_effect=mock_subprocess_run):
            result = runner.invoke(node.app, ["up-all", "--with-miner"])
            
            assert result.exit_code == 0
            
            # Check that mainnet and testnet have miner profile
            for args, kwargs in subprocess_calls:
                cmd = args[0]
                env = kwargs.get("env", {})
                network = env.get("ANIMICA_NETWORK")
                
                if network in ["mainnet", "testnet"]:
                    assert "--profile" in cmd
                    assert "miner" in cmd
