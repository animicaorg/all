"""Studio Services lifecycle and management CLI for Animica developers."""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
from pathlib import Path
from typing import Any, Dict, Optional

import httpx
import typer

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


def _get_compose_file() -> Path:
    """Get the path to the docker-compose file for devnet with studio services."""
    # Use the same compose file as node.py for consistency
    # This ensures we're working with the same service definitions
    repo_root = Path(__file__).resolve().parents[3]
    compose_file = repo_root / "tests" / "devnet" / "docker-compose.yml"
    
    if not compose_file.exists():
        typer.echo(
            f"Error: Docker Compose file not found at {compose_file}",
            err=True
        )
        typer.echo(
            "Studio Services lifecycle management requires the devnet docker-compose setup.",
            err=True
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
    rpc_url = rpc_url or os.getenv("RPC_URL") or os.getenv("ANIMICA_RPC_URL")
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
    timeout: float = 5.0
) -> Dict[str, Any]:
    """
    Check studio services health endpoint.
    
    Returns health status dict or raises exception.
    """
    url = f"http://{host}:{port}/healthz"
    async with httpx.AsyncClient(timeout=timeout) as client:
        response = await client.get(url)
        response.raise_for_status()
        return response.json() if response.headers.get("content-type", "").startswith("application/json") else DEFAULT_HEALTH_RESPONSE


async def _check_readiness(
    host: str = "127.0.0.1",
    port: int = DEFAULT_STUDIO_PORT,
    timeout: float = 5.0
) -> Dict[str, Any]:
    """
    Check studio services readiness endpoint.
    
    Returns readiness status dict or raises exception.
    """
    url = f"http://{host}:{port}/readyz"
    async with httpx.AsyncClient(timeout=timeout) as client:
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
    
    compose_file = _get_compose_file()
    
    typer.secho(f"\nStarting Studio Services for network: {network}", fg=typer.colors.CYAN, bold=True)
    typer.echo(f"Using compose file: {compose_file}")
    typer.echo("Note: This will also start the node if it's not already running.")
    
    # Build docker-compose command using both 'dev' and 'studio' profiles
    # 'dev' profile: node + miner (required dependencies)
    # 'studio' profile: studio-services + explorer
    cmd = [
        "docker", "compose",
        "-f", str(compose_file),
        "--profile", "dev",
        "--profile", "studio",
    ]
    
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
        result = subprocess.run(
            cmd,
            cwd=compose_file.parent,
            check=False,
            env=env
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
    
    compose_file = _get_compose_file()
    
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
    cmd = [
        "docker", "compose",
        "-f", str(compose_file),
        "stop", "services", "explorer"
    ]
    
    # If removing volumes, we need to use 'rm' instead
    if volumes:
        cmd = [
            "docker", "compose",
            "-f", str(compose_file),
            "rm", "-f", "-s", "-v", "services", "explorer"
        ]
    
    if volumes:
        cmd.append("-v")
    
    typer.echo(f"\nRunning: {' '.join(cmd)}\n")
    
    try:
        result = subprocess.run(
            cmd,
            cwd=compose_file.parent,
            check=False,
            env={**os.environ, "ANIMICA_NETWORK": network}
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
    
    compose_file = _get_compose_file()
    
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
        # Run interactively to preserve output streaming
        subprocess.run(
            cmd,
            cwd=compose_file.parent,
            env={**os.environ, "ANIMICA_NETWORK": network}
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


if __name__ == "__main__":  # pragma: no cover
    app()
