"""Studio Services lifecycle and management CLI for Animica developers."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import shlex
import shutil
import subprocess
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

import httpx
import typer
from animica.config import get_network_defaults
from .timeouts import DEFAULT_RPC_TIMEOUT, RPC_TIMEOUT_ENV, resolve_timeout

from .state import get_cli_state

STATE_KEY_NETWORK = "active_network"
DEFAULT_STUDIO_PORT = 8081
DEFAULT_STUDIO_HOST = "0.0.0.0"
DEFAULT_CHAIN_ID = 1337
DEFAULT_STORAGE_DIR = "./.data"

# Default health check responses
DEFAULT_HEALTH_RESPONSE = {"status": "ok"}
DEFAULT_READY_RESPONSE = {"status": "ready"}

app = typer.Typer(help="Manage Animica Studio Services.")
LOGGER = logging.getLogger(__name__)
ELEVATED_COMMAND_TIMEOUT_SECONDS = 120


class PrivilegeActionType(str, Enum):
    DOCKER_PERMISSION = "DOCKER_PERMISSION"
    HOST_DIR_PERMISSION = "HOST_DIR_PERMISSION"
    MOUNT_PERMISSION = "MOUNT_PERMISSION"
    OTHER = "OTHER"


@dataclass
class PrivilegeCheckResult:
    needs_docker_access: bool
    needs_host_data_dir_write: bool
    needs_privileged_port: bool


class PrivilegeChecker:
    """Best-effort checks for whether a given operation likely needs elevation."""

    def evaluate(self, host_dir: Optional[str], port: Optional[int]) -> PrivilegeCheckResult:
        return PrivilegeCheckResult(
            needs_docker_access=not self._can_access_docker(),
            needs_host_data_dir_write=not self._can_write_host_dir(host_dir),
            needs_privileged_port=(port is not None and port < 1024),
        )

    def classify_error(self, stderr: str) -> PrivilegeActionType:
        lowered = (stderr or "").lower()
        if "docker.sock" in lowered or "permission denied while trying to connect" in lowered:
            return PrivilegeActionType.DOCKER_PERMISSION
        if "mount" in lowered:
            return PrivilegeActionType.MOUNT_PERMISSION
        if "eacces" in lowered or "eperm" in lowered or "errno 13" in lowered or "permission denied" in lowered:
            return PrivilegeActionType.HOST_DIR_PERMISSION
        return PrivilegeActionType.OTHER

    def _can_access_docker(self) -> bool:
        docker_sock = Path("/var/run/docker.sock")
        return docker_sock.exists() and os.access(docker_sock, os.R_OK | os.W_OK)

    def _can_write_host_dir(self, host_dir: Optional[str]) -> bool:
        if not host_dir:
            return True
        candidate = Path(host_dir).expanduser().resolve()
        parent = candidate if candidate.exists() else candidate.parent
        return os.access(parent, os.W_OK)


class ElevatedRunner:
    """Run a single action with pkexec/sudo without re-execing the full app."""

    def run(self, argv: Sequence[str], env: Dict[str, str], cwd: Optional[Path]) -> subprocess.CompletedProcess[str]:
        if shutil.which("pkexec"):
            full_cmd: List[str] = ["pkexec", *argv]
        else:
            subprocess.run(["sudo", "-k"], check=False)
            full_cmd = ["sudo", "--preserve-env=PATH", *argv]

        return subprocess.run(
            full_cmd,
            cwd=str(cwd) if cwd else None,
            env=env,
            capture_output=True,
            text=True,
            check=False,
            timeout=ELEVATED_COMMAND_TIMEOUT_SECONDS,
        )


def _redact_sensitive_args(argv: Sequence[str]) -> List[str]:
    redacted: List[str] = []
    sensitive_keys = ("token", "password", "secret", "key")
    for arg in argv:
        lowered = arg.lower()
        if any(key in lowered for key in sensitive_keys):
            if "=" in arg:
                redacted.append(f"{arg.split('=', 1)[0]}=<redacted>")
            else:
                redacted.append("<redacted>")
        else:
            redacted.append(arg)
    return redacted


def _recommended_non_sudo_fixes(checks: PrivilegeCheckResult, storage_dir: Optional[str]) -> List[str]:
    fixes: List[str] = []
    if checks.needs_docker_access:
        fixes.append("Add your user to docker group: sudo usermod -aG docker $USER && newgrp docker")
    if checks.needs_host_data_dir_write and storage_dir:
        fixes.append(f"Use a user-writable directory (recommended): ~/.animica/studio instead of {storage_dir}")
    if checks.needs_privileged_port:
        fixes.append("Use an unprivileged port (>=1024), e.g. --port 8081")
    if not fixes:
        fixes.append("Retry the operation and inspect diagnostics; permission issue may be transient.")
    return fixes


def _prompt_privilege_required(
    action_type: PrivilegeActionType,
    explanation: str,
    command_preview: Sequence[str],
    checks: PrivilegeCheckResult,
    storage_dir: Optional[str],
) -> str:
    typer.secho("\nAdministrator privileges required", fg=typer.colors.YELLOW, bold=True)
    typer.echo(explanation)
    typer.echo(f"Action type: {action_type.value}")
    typer.echo(f"Command preview: {shlex.join(command_preview)}")
    typer.echo("\nChoose an option:")
    typer.echo("  1) Fix without sudo (recommended)")
    typer.echo("  2) Run this action with sudo…")
    typer.echo("  3) Cancel")
    remember_for_session = typer.confirm("Remember for this session?", default=False)
    if remember_for_session:
        typer.echo("Session remember enabled for this process only.")
    choice = typer.prompt("Select 1, 2, or 3", default="1").strip()
    if choice == "1":
        typer.secho("\nRecommended non-sudo fixes:", fg=typer.colors.CYAN, bold=True)
        for fix in _recommended_non_sudo_fixes(checks, storage_dir):
            typer.echo(f"  - {fix}")
        typer.echo("\nCopy diagnostics: include command output and permission error details.")
    return choice


def _run_with_optional_elevation(
    *,
    cmd: Sequence[str],
    env: Dict[str, str],
    cwd: Path,
    host_dir: Optional[str],
    port: Optional[int],
    feature_name: str,
) -> subprocess.CompletedProcess[str]:
    """Run command unprivileged first, then optionally elevate a single action."""
    result = subprocess.run(
        list(cmd),
        cwd=cwd,
        check=False,
        env=env,
        capture_output=True,
        text=True,
    )
    if result.stdout:
        typer.echo(result.stdout.rstrip())
    if result.stderr:
        typer.echo(result.stderr.rstrip(), err=True)
    if result.returncode == 0:
        return result

    checker = PrivilegeChecker()
    action_type = checker.classify_error(result.stderr)
    permission_like_error = action_type != PrivilegeActionType.OTHER
    if not permission_like_error:
        return result

    checks = checker.evaluate(host_dir=host_dir, port=port)
    choice = _prompt_privilege_required(
        action_type=action_type,
        explanation=(
            f"{feature_name} hit a permission error. Studio stays unprivileged, "
            "but this specific action can be elevated after your explicit confirmation."
        ),
        command_preview=cmd,
        checks=checks,
        storage_dir=host_dir,
    )
    if choice != "2":
        raise typer.Exit(code=1)

    redacted = _redact_sensitive_args(cmd)
    LOGGER.warning("Elevated action requested | action_type=%s | argv=%s", action_type.value, redacted)
    elevated_runner = ElevatedRunner()
    try:
        elevated = elevated_runner.run(argv=cmd, env=env, cwd=cwd)
    except subprocess.TimeoutExpired:
        typer.echo(
            f"Elevated command timed out after {ELEVATED_COMMAND_TIMEOUT_SECONDS} seconds.",
            err=True,
        )
        raise typer.Exit(code=1)

    if elevated.stdout:
        typer.echo(elevated.stdout.rstrip())
    if elevated.stderr:
        typer.echo(elevated.stderr.rstrip(), err=True)
    return elevated


def _ensure_network_set() -> str:
    """
    Ensure a network is configured before performing studio operations.
    
    Returns the active network name if set.
    Exits with error message if no network is configured.
    """
    # Check environment variable first (highest priority)
    network = os.environ.get("ANIMICA_NETWORK")
    if network:
        return network
    
    # Check persisted state
    state = get_cli_state()
    network = state.get(STATE_KEY_NETWORK)
    if network:
        return network
    
    # No network configured - exit with helpful message
    typer.echo(
        "Error: No network configured. Studio Services operations require a network to be set.",
        err=True
    )
    typer.echo("\nPlease set a network first using one of these methods:", err=True)
    typer.echo("  1. Set persistent network: animica network set <network>", err=True)
    typer.echo("  2. Set via environment: export ANIMICA_NETWORK=<network>", err=True)
    typer.echo("  3. Use --network flag: animica --network <network> studio up", err=True)
    typer.echo("\nAvailable networks: mainnet, testnet, devnet, local-devnet", err=True)
    raise typer.Exit(code=1)


def _get_compose_file(network: str) -> Path:
    """
    Get the path to the docker-compose file for the specified network.
    
    Args:
        network: Network name (mainnet, testnet, devnet, local-devnet)
        
    Returns:
        Path to the appropriate docker-compose file
        
    Raises:
        typer.Exit: If compose file not found
    """
    defaults = get_network_defaults(network)
    compose_file = defaults["compose_file"]
    
    if not compose_file.exists():
        typer.echo(
            f"Error: Docker Compose file not found at {compose_file}",
            err=True
        )
        typer.echo(
            f"Studio Services lifecycle management for {network} requires the compose setup.",
            err=True
        )
        typer.echo(
            f"\nExpected location: {compose_file}",
            err=True
        )
        typer.echo(
            "\nThis command requires the Animica source repository on disk "
            "(docker compose builds images from `context: ../..`).",
            err=True,
        )
        env_root = os.environ.get("ANIMICA_REPO_ROOT")
        if env_root:
            typer.echo(
                f"ANIMICA_REPO_ROOT is set to {env_root!r} but does not contain "
                "the expected ops/docker/ tree. Point it at a checkout of the repo.",
                err=True,
            )
        else:
            typer.echo(
                "If you installed animica via pip, set ANIMICA_REPO_ROOT to a "
                "checkout of the repo:",
                err=True,
            )
            typer.echo(
                "  export ANIMICA_REPO_ROOT=/path/to/animica",
                err=True,
            )
        raise typer.Exit(code=1)

    return compose_file


def _validate_config(
    rpc_url: Optional[str] = None,
    chain_id: Optional[int] = None,
    storage_dir: Optional[str] = None,
    host: Optional[str] = None,
    port: Optional[int] = None,
) -> Dict[str, Any]:
    """
    Validate studio services configuration from environment/args.
    
    Returns a dict of validated config values.
    Raises typer.Exit if required config is missing or invalid.
    """
    errors = []
    config = {}
    
    # RPC_URL (required)
    # Handle empty strings by treating them as unset
    if not (rpc_url and rpc_url.strip()):
        rpc_url = None
    if not rpc_url:
        env_rpc = os.getenv("RPC_URL")
        if env_rpc and env_rpc.strip():
            rpc_url = env_rpc.strip()
    if not rpc_url:
        env_animica_rpc = os.getenv("ANIMICA_RPC_URL")
        if env_animica_rpc and env_animica_rpc.strip():
            rpc_url = env_animica_rpc.strip()
    if not rpc_url:
        errors.append("RPC_URL is required. Set via --rpc-url or RPC_URL environment variable.")
    else:
        config["RPC_URL"] = rpc_url
    
    # CHAIN_ID (defaults to 1337)
    if chain_id is not None:
        config["CHAIN_ID"] = chain_id
    else:
        chain_id_str = os.getenv("CHAIN_ID") or os.getenv("ANIMICA_CHAIN_ID")
        if chain_id_str:
            try:
                config["CHAIN_ID"] = int(chain_id_str)
            except (ValueError, TypeError):
                errors.append(f"CHAIN_ID must be a valid integer, got: '{chain_id_str}'")
        else:
            config["CHAIN_ID"] = DEFAULT_CHAIN_ID
    
    # STORAGE_DIR (defaults to ./.data)
    storage_dir = storage_dir or os.getenv("STORAGE_DIR") or DEFAULT_STORAGE_DIR
    config["STORAGE_DIR"] = storage_dir
    
    # HOST (defaults to 0.0.0.0)
    host = host or os.getenv("HOST") or os.getenv("SERVICES_HOST") or DEFAULT_STUDIO_HOST
    config["HOST"] = host
    
    # PORT (defaults to 8081)
    if port is not None:
        config["PORT"] = port
    else:
        port_str = os.getenv("PORT") or os.getenv("SERVICES_PORT")
        if port_str:
            try:
                config["PORT"] = int(port_str)
            except (ValueError, TypeError):
                errors.append(f"PORT must be a valid integer, got: '{port_str}'")
        else:
            config["PORT"] = DEFAULT_STUDIO_PORT
    
    # Optional but recommended: ALLOWED_ORIGINS for CORS
    allowed_origins = os.getenv("ALLOWED_ORIGINS") or os.getenv("CORS_ALLOW_ORIGINS")
    if allowed_origins:
        config["ALLOWED_ORIGINS"] = allowed_origins
    
    # Optional: FAUCET_KEY (for dev/test only)
    faucet_key = os.getenv("FAUCET_KEY")
    if faucet_key:
        config["FAUCET_KEY"] = faucet_key
    
    # Optional: RATE_LIMITS
    rate_limits = os.getenv("RATE_LIMITS")
    if rate_limits:
        config["RATE_LIMITS"] = rate_limits
    
    if errors:
        typer.echo("Configuration validation failed:\n", err=True)
        for error in errors:
            typer.echo(f"  ✗ {error}", err=True)
        typer.echo("\nPlease set the required environment variables or use CLI options.", err=True)
        raise typer.Exit(code=1)
    
    return config


async def _check_health(
    host: str = "127.0.0.1",
    port: int = DEFAULT_STUDIO_PORT,
    timeout: Optional[float] = None,
) -> Dict[str, Any]:
    """
    Check studio services health endpoint.
    
    Returns health status dict or raises exception.
    """
    url = f"http://{host}:{port}/healthz"
    resolved_timeout = resolve_timeout("RPC timeout", timeout, env_var=RPC_TIMEOUT_ENV, default=DEFAULT_RPC_TIMEOUT)
    async with httpx.AsyncClient(timeout=resolved_timeout) as client:
        response = await client.get(url)
        response.raise_for_status()
        return response.json() if response.headers.get("content-type", "").startswith("application/json") else DEFAULT_HEALTH_RESPONSE


async def _check_readiness(
    host: str = "127.0.0.1",
    port: int = DEFAULT_STUDIO_PORT,
    timeout: Optional[float] = None,
) -> Dict[str, Any]:
    """
    Check studio services readiness endpoint.
    
    Returns readiness status dict or raises exception.
    """
    url = f"http://{host}:{port}/readyz"
    resolved_timeout = resolve_timeout("RPC timeout", timeout, env_var=RPC_TIMEOUT_ENV, default=DEFAULT_RPC_TIMEOUT)
    async with httpx.AsyncClient(timeout=resolved_timeout) as client:
        response = await client.get(url)
        response.raise_for_status()
        return response.json() if response.headers.get("content-type", "").startswith("application/json") else DEFAULT_READY_RESPONSE


@app.command("up")
def up(
    detach: bool = typer.Option(
        True,
        "--detach/--no-detach",
        help="Run in detached mode (background)"
    ),
    build: bool = typer.Option(
        True,
        "--build/--no-build",
        help="Build images before starting"
    ),
    rpc_url: Optional[str] = typer.Option(
        None,
        "--rpc-url",
        help="Override RPC endpoint URL",
        envvar="RPC_URL"
    ),
    chain_id: Optional[int] = typer.Option(
        None,
        "--chain-id",
        help="Override chain ID",
        envvar="CHAIN_ID"
    ),
    storage_dir: Optional[str] = typer.Option(
        None,
        "--storage-dir",
        help="Storage directory for artifacts",
        envvar="STORAGE_DIR"
    ),
) -> None:
    """
    Start Animica Studio Services using Docker Compose.
    
    This command spins up Studio Services with the configured network settings.
    It uses the devnet docker-compose configuration which includes:
      - Studio Services API (deploy, verify, artifacts, faucet)
      - Background workers for verification queue
      - SQLite database and file storage
      - Metrics and health endpoints
      - Explorer web interface (optional)
    
    Studio Services depends on an Animica node being available. If the node is
    not already running, this command will start it automatically along with
    Studio Services. Alternatively, start the node first with 'animica node up'.
    
    Before running this command, ensure you have set a network using:
      animica network set <network>
    
    Required Configuration:
      - RPC_URL: Node JSON-RPC endpoint (default: http://127.0.0.1:8545)
      - CHAIN_ID: Network chain ID (default: 1337)
    
    Examples:
      animica studio up
      animica studio up --no-detach  # Run in foreground
      animica studio up --rpc-url http://localhost:8545 --chain-id 1337
    """
    # Enforce network requirement
    network = _ensure_network_set()
    
    # Validate configuration
    typer.secho("Validating configuration...", fg=typer.colors.CYAN)
    config = _validate_config(
        rpc_url=rpc_url,
        chain_id=chain_id,
        storage_dir=storage_dir
    )
    
    typer.secho("✓ Configuration valid", fg=typer.colors.GREEN)
    typer.echo(f"  RPC_URL: {config['RPC_URL']}")
    typer.echo(f"  CHAIN_ID: {config['CHAIN_ID']}")
    typer.echo(f"  STORAGE_DIR: {config['STORAGE_DIR']}")
    typer.echo(f"  HOST: {config['HOST']}")
    typer.echo(f"  PORT: {config['PORT']}")
    
    # Get network-specific compose file
    compose_file = _get_compose_file(network)
    
    typer.secho(f"\nStarting Studio Services for network: {network}", fg=typer.colors.CYAN, bold=True)
    typer.echo(f"Using compose file: {compose_file}")
    typer.echo("Note: This will also start the node if it's not already running.")
    
    # Build docker-compose command
    # For devnet, use both 'dev' (node+miner) and 'studio' (services+explorer) profiles
    # For mainnet/testnet, just use 'studio' profile (node runs by default)
    cmd = [
        "docker", "compose",
        "-f", str(compose_file),
    ]
    
    if network in ["devnet", "local-devnet"]:
        # Devnet needs both profiles
        cmd.extend(["--profile", "dev", "--profile", "studio"])
    else:
        # Mainnet/testnet: studio profile includes services+explorer
        cmd.extend(["--profile", "studio"])
    
    # Note: We removed the separate --profile flag as it's now automatic
    
    if build:
        cmd.extend(["up", "--build"])
    else:
        cmd.append("up")
    
    if detach:
        cmd.append("-d")
    
    typer.echo(f"\nRunning: {' '.join(cmd)}")
    typer.echo("This may take a few minutes on first run...\n")
    
    # Build environment with config overrides (ensure all values are strings)
    env = {**os.environ, "ANIMICA_NETWORK": network}
    for key, value in config.items():
        env[key] = str(value)
    
    try:
        result = _run_with_optional_elevation(
            cmd=cmd,
            cwd=compose_file.parent,
            env=env,
            host_dir=config.get("STORAGE_DIR"),
            port=config.get("PORT"),
            feature_name="Starting Studio Services",
        )
        
        if result.returncode == 0:
            typer.secho("✓ Studio Services started successfully!", fg=typer.colors.GREEN, bold=True)
            if detach:
                typer.echo("\nStudio Services is running in the background.")
                typer.echo(f"API available at: http://127.0.0.1:{config['PORT']}")
                typer.echo("View logs with: animica studio logs")
                typer.echo("Check status with: animica studio status")
                typer.echo(f"OpenAPI docs at: http://127.0.0.1:{config['PORT']}/docs")
        else:
            typer.secho(
                f"Error: Studio Services startup failed with exit code {result.returncode}",
                fg=typer.colors.RED,
                err=True
            )
            raise typer.Exit(code=result.returncode)
            
    except FileNotFoundError:
        typer.echo(
            "Error: 'docker' command not found. Please install Docker and Docker Compose.",
            err=True
        )
        raise typer.Exit(code=1)
    except KeyboardInterrupt:
        typer.echo("\n\nInterrupted by user", err=True)
        raise typer.Exit(code=130)


@app.command("down")
def down(
    volumes: bool = typer.Option(
        False,
        "--volumes",
        "-v",
        help="Remove volumes (WARNING: deletes storage data)"
    ),
) -> None:
    """
    Stop and tear down Studio Services.
    
    This command stops Studio Services and optionally removes associated volumes.
    By default, storage data (artifacts, database) is preserved unless --volumes
    flag is used.
    
    Before running this command, ensure you have set a network using:
      animica network set <network>
    
    Examples:
      animica studio down
      animica studio down --volumes  # Also delete storage data
    
    WARNING: Using --volumes will delete all storage data including artifacts
    and the verification database!
    """
    # Enforce network requirement
    network = _ensure_network_set()
    
    # Get network-specific compose file
    compose_file = _get_compose_file(network)
    
    typer.secho(f"Stopping Studio Services for network: {network}", fg=typer.colors.CYAN, bold=True)
    typer.echo(f"Using compose file: {compose_file}")
    typer.echo("Note: This will stop only Studio Services. Use 'animica node down' to stop the node.")
    
    if volumes:
        typer.secho(
            "\n⚠ WARNING: --volumes flag will delete all storage data!",
            fg=typer.colors.YELLOW,
            bold=True
        )
    
    # Build docker-compose command to stop only 'studio' profile services
    # We specify the services explicitly to avoid stopping the node
    if volumes:
        # Use 'rm' command with force and volumes flags to remove containers and volumes
        cmd = [
            "docker", "compose",
            "-f", str(compose_file),
            "rm", "-f", "-v", "services", "explorer"
        ]
    else:
        # Use 'stop' command to just stop the containers
        cmd = [
            "docker", "compose",
            "-f", str(compose_file),
            "stop", "services", "explorer"
        ]
    
    typer.echo(f"\nRunning: {' '.join(cmd)}\n")
    
    try:
        result = _run_with_optional_elevation(
            cmd=cmd,
            cwd=compose_file.parent,
            env={**os.environ, "ANIMICA_NETWORK": network},
            host_dir=None,
            port=None,
            feature_name="Stopping Studio Services",
        )
        
        if result.returncode == 0:
            typer.secho("✓ Studio Services stopped successfully!", fg=typer.colors.GREEN, bold=True)
            if volumes:
                typer.echo("All volumes and storage data have been removed.")
            else:
                typer.echo("Storage data has been preserved in volumes.")
                typer.echo("Use 'animica studio down --volumes' to remove data.")
        else:
            typer.secho(
                f"Error: Studio Services shutdown failed with exit code {result.returncode}",
                fg=typer.colors.RED,
                err=True
            )
            raise typer.Exit(code=result.returncode)
            
    except FileNotFoundError:
        typer.echo(
            "Error: 'docker' command not found. Please install Docker and Docker Compose.",
            err=True
        )
        raise typer.Exit(code=1)
    except KeyboardInterrupt:
        typer.echo("\n\nInterrupted by user", err=True)
        raise typer.Exit(code=130)


@app.command("status")
def status(
    host: str = typer.Option(
        "127.0.0.1",
        "--host",
        help="Studio Services host",
        envvar="HOST"
    ),
    port: int = typer.Option(
        DEFAULT_STUDIO_PORT,
        "--port",
        help="Studio Services port",
        envvar="PORT"
    ),
    json_output: bool = typer.Option(
        False,
        "--json/--no-json",
        help="Output JSON instead of human-readable text"
    ),
) -> None:
    """
    Check Studio Services health and status.
    
    This command queries the /healthz and /readyz endpoints to check if
    Studio Services is running and ready to serve requests.
    
    Examples:
      animica studio status
      animica studio status --json
      animica studio status --host 127.0.0.1 --port 8081
    """
    try:
        # Check health
        health = asyncio.run(_check_health(host, port))
        ready = asyncio.run(_check_readiness(host, port))
        
        if json_output:
            output = {
                "host": host,
                "port": port,
                "health": health,
                "ready": ready,
                "status": "running"
            }
            typer.echo(json.dumps(output, indent=2))
        else:
            typer.secho("✓ Studio Services is running", fg=typer.colors.GREEN, bold=True)
            typer.echo(f"  Host: {host}")
            typer.echo(f"  Port: {port}")
            typer.echo(f"  Health: {health.get('status', 'ok')}")
            typer.echo(f"  Ready: {ready.get('status', 'ready')}")
            typer.echo(f"\nAPI available at: http://{host}:{port}")
            typer.echo(f"OpenAPI docs at: http://{host}:{port}/docs")
            
    except httpx.ConnectError:
        if json_output:
            output = {
                "host": host,
                "port": port,
                "status": "not_running",
                "error": "Connection refused"
            }
            typer.echo(json.dumps(output, indent=2))
        else:
            typer.secho("✗ Studio Services is not running", fg=typer.colors.RED, bold=True)
            typer.echo(f"  Could not connect to http://{host}:{port}")
            typer.echo("\nStart Studio Services with: animica studio up")
        raise typer.Exit(code=1)
    except httpx.TimeoutException:
        if json_output:
            output = {
                "host": host,
                "port": port,
                "status": "timeout",
                "error": "Request timeout"
            }
            typer.echo(json.dumps(output, indent=2))
        else:
            typer.secho("✗ Studio Services is not responding", fg=typer.colors.YELLOW, bold=True)
            typer.echo(f"  Request to http://{host}:{port} timed out")
        raise typer.Exit(code=1)
    except Exception as e:
        if json_output:
            output = {
                "host": host,
                "port": port,
                "status": "error",
                "error": str(e)
            }
            typer.echo(json.dumps(output, indent=2))
        else:
            typer.secho(f"✗ Error checking status: {e}", fg=typer.colors.RED, bold=True, err=True)
        raise typer.Exit(code=1)


@app.command("logs")
def logs(
    follow: bool = typer.Option(
        False,
        "--follow",
        "-f",
        help="Follow log output"
    ),
    tail: int = typer.Option(
        100,
        "--tail",
        "-n",
        help="Number of lines to show from the end"
    ),
) -> None:
    """
    View Studio Services logs.
    
    This command displays logs from the Studio Services container using
    docker-compose logs.
    
    Examples:
      animica studio logs
      animica studio logs --follow  # Tail logs continuously
      animica studio logs --tail 50  # Show last 50 lines
    """
    # Enforce network requirement
    network = _ensure_network_set()
    
    # Get network-specific compose file
    compose_file = _get_compose_file(network)
    
    # Build docker-compose command to view logs for studio services only
    cmd = [
        "docker", "compose",
        "-f", str(compose_file),
        "logs",
        "--tail", str(tail),
    ]
    
    if follow:
        cmd.append("-f")
    
    # Target only studio services
    cmd.extend(["services", "explorer"])
    
    try:
        _run_with_optional_elevation(
            cmd=cmd,
            cwd=compose_file.parent,
            env={**os.environ, "ANIMICA_NETWORK": network},
            host_dir=None,
            port=None,
            feature_name="Viewing Studio Services logs",
        )
    except FileNotFoundError:
        typer.echo(
            "Error: 'docker' command not found. Please install Docker and Docker Compose.",
            err=True
        )
        raise typer.Exit(code=1)
    except KeyboardInterrupt:
        typer.echo("\n\nLog streaming stopped", err=True)
        raise typer.Exit(code=0)


@app.command("config")
def config_validate(
    rpc_url: Optional[str] = typer.Option(
        None,
        "--rpc-url",
        help="RPC endpoint URL",
        envvar="RPC_URL"
    ),
    chain_id: Optional[int] = typer.Option(
        None,
        "--chain-id",
        help="Chain ID",
        envvar="CHAIN_ID"
    ),
    storage_dir: Optional[str] = typer.Option(
        None,
        "--storage-dir",
        help="Storage directory",
        envvar="STORAGE_DIR"
    ),
    host: Optional[str] = typer.Option(
        None,
        "--host",
        help="Bind host",
        envvar="HOST"
    ),
    port: Optional[int] = typer.Option(
        None,
        "--port",
        help="Bind port",
        envvar="PORT"
    ),
) -> None:
    """
    Validate Studio Services configuration without starting the service.
    
    This command checks that all required configuration is present and valid,
    including environment variables and optional settings.
    
    Required:
      - RPC_URL: Node JSON-RPC endpoint
      
    Optional:
      - CHAIN_ID: Network chain ID (default: 1337)
      - STORAGE_DIR: Storage directory (default: ./.data)
      - HOST: Bind host (default: 0.0.0.0)
      - PORT: Bind port (default: 8081)
      - ALLOWED_ORIGINS: CORS allowed origins (comma-separated)
      - FAUCET_KEY: Faucet private key (dev/test only)
      - RATE_LIMITS: Rate limit configuration (JSON)
    
    Examples:
      animica studio config
      animica studio config --rpc-url http://localhost:8545
    """
    typer.secho("Validating Studio Services configuration...\n", fg=typer.colors.CYAN)
    
    try:
        config = _validate_config(
            rpc_url=rpc_url,
            chain_id=chain_id,
            storage_dir=storage_dir,
            host=host,
            port=port
        )
        
        typer.secho("✓ Configuration is valid!\n", fg=typer.colors.GREEN, bold=True)
        
        typer.echo("Required configuration:")
        typer.echo(f"  RPC_URL: {config['RPC_URL']}")
        typer.echo(f"  CHAIN_ID: {config['CHAIN_ID']}")
        typer.echo(f"  STORAGE_DIR: {config['STORAGE_DIR']}")
        typer.echo(f"  HOST: {config['HOST']}")
        typer.echo(f"  PORT: {config['PORT']}")
        
        optional_keys = ["ALLOWED_ORIGINS", "FAUCET_KEY", "RATE_LIMITS"]
        optional_config = {k: v for k, v in config.items() if k in optional_keys}
        
        if optional_config:
            typer.echo("\nOptional configuration:")
            for key, value in optional_config.items():
                # Redact sensitive values
                if key == "FAUCET_KEY":
                    value = f"{value[:6]}...{value[-4:]}" if len(value) > 10 else "***"
                typer.echo(f"  {key}: {value}")
        
        typer.echo("\n✓ Studio Services is ready to start with this configuration")
        typer.echo("  Run: animica studio up")
        
    except typer.Exit:
        # Re-raise exit from validation
        raise
    except Exception as e:
        typer.secho(f"✗ Configuration validation failed: {e}", fg=typer.colors.RED, bold=True, err=True)
        raise typer.Exit(code=1)


# Attach the serverless-compute commands (run/deploy/serve/fn) to this same
# `animica studio` group. Kept in a separate module so this file stays focused
# on Studio Services lifecycle. Best-effort: a failure here must not break the
# lifecycle commands.
try:  # pragma: no cover - registration glue
    from .studio_serverless import register as _register_serverless

    _register_serverless(app)
except Exception as _exc:  # noqa: BLE001
    LOGGER.debug("studio serverless commands unavailable: %s", _exc)


if __name__ == "__main__":  # pragma: no cover
    app()
