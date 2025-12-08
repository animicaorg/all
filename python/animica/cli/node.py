"""Node lifecycle and inspection CLI for Animica developers."""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
from pathlib import Path
from typing import Any, Dict, Optional

import httpx
import typer
from animica.config import load_network_config

from .state import get_cli_state

DEFAULT_RPC_URL = load_network_config().rpc_url
RPC_ENV = "ANIMICA_RPC_URL"
STATE_KEY_NETWORK = "active_network"

app = typer.Typer(help="Manage and query Animica nodes.")


async def rpc_call(
    method: str, params: Optional[list[Any]] = None, *, rpc_url: str
) -> Any:
    payload: Dict[str, Any] = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": method,
        "params": params or [],
    }
    async with httpx.AsyncClient(timeout=10.0) as client:
        response = await client.post(rpc_url, json=payload)
        data = response.json()
    if "error" in data:
        raise RuntimeError(data["error"])
    return data.get("result")


def _resolve_rpc_url(rpc_url: Optional[str]) -> str:
    return rpc_url or os.environ.get(RPC_ENV) or load_network_config().rpc_url


def _pretty(obj: Any) -> str:
    return json.dumps(obj, indent=2)


def _ensure_network_set() -> str:
    """
    Ensure a network is configured before performing node operations.
    
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
        "Error: No network configured. Node lifecycle operations require a network to be set.",
        err=True
    )
    typer.echo("\nPlease set a network first using one of these methods:", err=True)
    typer.echo("  1. Set persistent network: animica network set <network>", err=True)
    typer.echo("  2. Set via environment: export ANIMICA_NETWORK=<network>", err=True)
    typer.echo("  3. Use --network flag: animica --network <network> node up", err=True)
    typer.echo("\nAvailable networks: mainnet, testnet, devnet, local-devnet", err=True)
    raise typer.Exit(code=1)


def _get_compose_file() -> Path:
    """Get the path to the docker-compose file for devnet."""
    # Assume we're in the repo root or python/ subdirectory
    repo_root = Path(__file__).resolve().parents[3]
    compose_file = repo_root / "tests" / "devnet" / "docker-compose.yml"
    
    if not compose_file.exists():
        typer.echo(
            f"Error: Docker Compose file not found at {compose_file}",
            err=True
        )
        typer.echo(
            "Node lifecycle management requires the devnet docker-compose setup.",
            err=True
        )
        raise typer.Exit(code=1)
    
    return compose_file


@app.command()
def status(
    rpc_url: Optional[str] = typer.Option(
        None, "--rpc-url", help="JSON-RPC endpoint", envvar=RPC_ENV
    )
) -> None:
    """Show chain head, block info and sync state."""
    url = _resolve_rpc_url(rpc_url)
    try:
        head = asyncio.run(rpc_call("chain.getHead", [], rpc_url=url))
        height = head.get("height") or head.get("number") or 0
        chain_id = head.get("chainId") or head.get("chain_id")
        head_hash = head.get("hash") or head.get("blockHash")
    except Exception as e:
        typer.echo(f"Error contacting RPC at {url}: {e}", err=True)
        return

    block = None
    if height is not None:
        try:
            block = asyncio.run(
                rpc_call("chain.getBlockByHeight", [height], rpc_url=url)
            )
        except Exception:
            block = None

    sync_status = None
    for method in ("node.syncStatus", "chain.syncing", "sync.isSyncing"):
        try:
            sync_status = asyncio.run(rpc_call(method, [], rpc_url=url))
            break
        except Exception:
            continue

    typer.echo(f"RPC URL: {url}")
    typer.echo(f"Chain ID: {chain_id}")
    typer.echo(f"Head height: {height}")
    typer.echo(f"Head hash: {head_hash}")
    typer.echo(f"Sync status: {sync_status}")
    if block is not None:
        typer.echo("Head block:")
        typer.echo(_pretty(block))


@app.command()
def head(
    rpc_url: Optional[str] = typer.Option(
        None, "--rpc-url", help="JSON-RPC endpoint", envvar=RPC_ENV
    )
) -> None:
    """Print the current chain head summary."""
    url = _resolve_rpc_url(rpc_url)
    head_info = asyncio.run(rpc_call("chain.getHead", [], rpc_url=url))
    typer.echo(_pretty(head_info))


@app.command()
def block(
    height: Optional[int] = typer.Option(None, "--height", help="Block height"),
    hash: Optional[str] = typer.Option(None, "--hash", help="Block hash"),
    rpc_url: Optional[str] = typer.Option(
        None, "--rpc-url", help="JSON-RPC endpoint", envvar=RPC_ENV
    ),
) -> None:
    """Fetch and display a block by height or hash."""
    if not height and not hash:
        raise typer.BadParameter("Provide --height or --hash")
    url = _resolve_rpc_url(rpc_url)
    if height is not None:
        result = asyncio.run(rpc_call("chain.getBlockByHeight", [height], rpc_url=url))
        if (
            isinstance(result, dict)
            and "transactions" not in result
            and result.get("hash")
        ):
            result = asyncio.run(
                rpc_call("chain.getBlockByHash", [result["hash"]], rpc_url=url)
            )
    else:
        result = asyncio.run(rpc_call("chain.getBlockByHash", [hash], rpc_url=url))
    typer.echo(_pretty(result))


@app.command()
def tx(
    hash: str = typer.Option(..., "--hash", help="Transaction hash"),
    rpc_url: Optional[str] = typer.Option(
        None, "--rpc-url", help="JSON-RPC endpoint", envvar=RPC_ENV
    ),
) -> None:
    """Fetch and display a transaction by hash."""
    url = _resolve_rpc_url(rpc_url)
    result = asyncio.run(rpc_call("chain.getTransactionByHash", [hash], rpc_url=url))
    typer.echo(_pretty(result))


@app.command()
def up(
    profile: str = typer.Option(
        "dev",
        "--profile",
        help="Docker Compose profile to use (dev, prod, etc.)"
    ),
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
) -> None:
    """
    Start an Animica node using Docker Compose.
    
    This command spins up a local development node with the configured network
    settings. It uses the devnet docker-compose configuration to start:
      - Node(s) with RPC/WS endpoints
      - Optional mining service
      - Optional explorer and services
    
    Before running this command, ensure you have set a network using:
      animica network set <network>
    
    Examples:
      animica node up
      animica node up --no-detach  # Run in foreground
      animica node up --profile dev --build
    """
    # Enforce network requirement
    network = _ensure_network_set()
    
    compose_file = _get_compose_file()
    
    typer.secho(f"Starting node for network: {network}", fg=typer.colors.CYAN, bold=True)
    typer.echo(f"Using compose file: {compose_file}")
    typer.echo(f"Profile: {profile}")
    
    # Build docker-compose command
    cmd = [
        "docker", "compose",
        "-f", str(compose_file),
        "--profile", profile,
    ]
    
    if build:
        cmd.extend(["up", "--build"])
    else:
        cmd.append("up")
    
    if detach:
        cmd.append("-d")
    
    typer.echo(f"\nRunning: {' '.join(cmd)}")
    typer.echo("This may take a few minutes on first run...\n")
    
    try:
        result = subprocess.run(
            cmd,
            cwd=compose_file.parent,
            check=False,
            env={**os.environ, "ANIMICA_NETWORK": network}
        )
        
        if result.returncode == 0:
            typer.secho("✓ Node started successfully!", fg=typer.colors.GREEN, bold=True)
            if detach:
                typer.echo("\nNode is running in the background.")
                typer.echo("View logs with: docker compose -f tests/devnet/docker-compose.yml logs -f")
                typer.echo("Check status with: animica node status")
        else:
            typer.secho(
                f"Error: Node startup failed with exit code {result.returncode}",
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


@app.command()
def down(
    profile: str = typer.Option(
        "dev",
        "--profile",
        help="Docker Compose profile to use (dev, prod, etc.)"
    ),
    volumes: bool = typer.Option(
        False,
        "--volumes",
        "-v",
        help="Remove volumes (WARNING: deletes blockchain data)"
    ),
) -> None:
    """
    Stop and tear down an Animica node.
    
    This command stops all services started by 'node up' and optionally
    removes associated volumes. By default, blockchain data is preserved
    unless --volumes flag is used.
    
    Before running this command, ensure you have set a network using:
      animica network set <network>
    
    Examples:
      animica node down
      animica node down --volumes  # Also delete blockchain data
      animica node down --profile dev
    
    WARNING: Using --volumes will delete all blockchain data!
    """
    # Enforce network requirement
    network = _ensure_network_set()
    
    compose_file = _get_compose_file()
    
    typer.secho(f"Stopping node for network: {network}", fg=typer.colors.CYAN, bold=True)
    typer.echo(f"Using compose file: {compose_file}")
    typer.echo(f"Profile: {profile}")
    
    if volumes:
        typer.secho(
            "\n⚠ WARNING: --volumes flag will delete all blockchain data!",
            fg=typer.colors.YELLOW,
            bold=True
        )
    
    # Build docker-compose command
    cmd = [
        "docker", "compose",
        "-f", str(compose_file),
        "--profile", profile,
        "down"
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
            typer.secho("✓ Node stopped successfully!", fg=typer.colors.GREEN, bold=True)
            if volumes:
                typer.echo("All volumes and blockchain data have been removed.")
            else:
                typer.echo("Blockchain data has been preserved in volumes.")
                typer.echo("Use 'animica node down --volumes' to remove data.")
        else:
            typer.secho(
                f"Error: Node shutdown failed with exit code {result.returncode}",
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


if __name__ == "__main__":  # pragma: no cover
    app()
