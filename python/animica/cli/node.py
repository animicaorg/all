"""Node lifecycle and inspection CLI for Animica developers."""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import time
from pathlib import Path
from typing import Any, Dict, Optional

import httpx
import typer
from animica.config import get_network_defaults, load_network_config

from .timeouts import DEFAULT_RPC_TIMEOUT, RPC_TIMEOUT_ENV, describe_timeout, resolve_timeout

from .state import get_cli_state

DEFAULT_RPC_URL = load_network_config().rpc_url
RPC_ENV = "ANIMICA_RPC_URL"
STATE_KEY_NETWORK = "active_network"

# Networks that use the 'dev' profile in docker-compose
DEV_NETWORKS = {"devnet", "local-devnet"}

BOOTSTRAP_TIMEOUT_ENV = "ANIMICA_BOOTSTRAP_TIMEOUT"
BOOTSTRAP_RPC_TIMEOUT = 30.0
ALLOWED_BOOTSTRAP_METHODS = {
    "bootstrap.getManifest",
    "bootstrap.getSeeds",
}

app = typer.Typer(help="Manage and query Animica nodes.")


async def rpc_call(
    method: str, params: Optional[list[Any]] = None, *, rpc_url: str, timeout: Optional[float] = None
) -> Any:
    resolved_timeout = resolve_timeout("RPC timeout", timeout, env_var=RPC_TIMEOUT_ENV)
    payload: Dict[str, Any] = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": method,
        "params": params or [],
    }
    async with httpx.AsyncClient(timeout=resolved_timeout) as client:
        response = await client.post(rpc_url, json=payload)
        data = response.json()
    if "error" in data:
        raise RuntimeError(data["error"])
    return data.get("result")


def _resolve_rpc_url(rpc_url: Optional[str]) -> str:
    """Resolve RPC URL from CLI arg, env var, or network config (defaults to mainnet).
    
    Empty strings are treated as unset and fall back to the next priority level.
    """
    # Check CLI argument first
    if rpc_url and rpc_url.strip():
        return rpc_url.strip()
    
    # Check environment variable
    env_url = os.environ.get(RPC_ENV)
    if env_url and env_url.strip():
        return env_url.strip()
    
    # Fall back to network config
    return load_network_config().rpc_url


def _pretty(obj: Any) -> str:
    return json.dumps(obj, indent=2)


def _db_path(cfg: Any) -> Path:
    data_dir = Path(os.path.expanduser(cfg.data_dir))
    data_dir.mkdir(parents=True, exist_ok=True)
    db_name = getattr(cfg, "db_name", "animica.db")
    return data_dir / db_name


def _bootstrap_state_path(cfg: Any) -> Path:
    data_dir = Path(os.path.expanduser(cfg.data_dir))
    data_dir.mkdir(parents=True, exist_ok=True)
    return data_dir / "bootstrap.json"


def _persist_bootstrap_state(cfg: Any, manifest: dict[str, Any], seeds: list[str]) -> Path:
    state_path = _bootstrap_state_path(cfg)
    payload = {"manifest": manifest, "seeds": seeds, "fetched_at": int(time.time())}
    state_path.write_text(json.dumps(payload, indent=2))
    return state_path


def _sync_state_path(cfg: Any) -> Path:
    data_dir = Path(os.path.expanduser(cfg.data_dir))
    data_dir.mkdir(parents=True, exist_ok=True)
    return data_dir / "sync" / "progress.json"


def _load_sync_state(cfg: Any) -> Optional[Dict[str, Any]]:
    state_path = _sync_state_path(cfg)
    if not state_path.exists():
        return None
    try:
        return json.loads(state_path.read_text())
    except Exception:
        return None


def _format_sync_timestamp(raw: Any) -> Optional[str]:
    if raw is None:
        return None
    try:
        return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(float(raw)))
    except (TypeError, ValueError):
        return None


def _bootstrap_rpc(bootstrap_url: str, method: str) -> Dict[str, Any]:
    if method not in ALLOWED_BOOTSTRAP_METHODS:
        raise ValueError(
            f"Unsupported bootstrap method '{method}'. Only read-only bootstrap RPC calls are permitted."
        )

    timeout = resolve_timeout(
        "bootstrap RPC timeout",
        None,
        env_var=BOOTSTRAP_TIMEOUT_ENV,
        default=BOOTSTRAP_RPC_TIMEOUT,
    )
    payload = {"jsonrpc": "2.0", "id": 1, "method": method, "params": []}
    try:
        resp = httpx.post(bootstrap_url, json=payload, timeout=timeout)
        resp.raise_for_status()
        parsed = resp.json()
        if "error" in parsed and parsed["error"]:
            raise RuntimeError(parsed["error"])
        result = parsed.get("result")
        return result if isinstance(result, dict) else {}
    except Exception as exc:
        raise RuntimeError(f"Bootstrap RPC {method} failed: {exc}") from exc


def _local_rpc(rpc_url: str, method: str, params: list[Any] | None = None) -> Any:
    payload = {"jsonrpc": "2.0", "id": 1, "method": method, "params": params or []}
    resp = httpx.post(rpc_url, json=payload, timeout=5.0)
    resp.raise_for_status()
    parsed = resp.json()
    if "error" in parsed and parsed["error"]:
        raise RuntimeError(parsed["error"])
    return parsed.get("result")


def _fetch_bootstrap_data(net_cfg: Any, bootstrap_url: str) -> tuple[dict[str, Any], list[str], Path]:
    manifest = _bootstrap_rpc(bootstrap_url, "bootstrap.getManifest")

    seeds: list[str] = []
    p2p_info = manifest.get("p2p") if isinstance(manifest, dict) else None
    if isinstance(p2p_info, dict):
        seeds = list(p2p_info.get("seeds") or [])

    if not seeds:
        try:
            seed_resp = _bootstrap_rpc(bootstrap_url, "bootstrap.getSeeds")
            seeds = list(seed_resp.get("seeds") or [])
        except Exception:
            seeds = []

    state_path = _persist_bootstrap_state(net_cfg, manifest, seeds)
    if seeds:
        os.environ["ANIMICA_P2P_SEEDS"] = ",".join(str(s) for s in seeds)
    return manifest, seeds, state_path


def _auto_bootstrap_if_needed(net_cfg: Any, bootstrap_url: str | None, *, force: bool = False, quiet: bool = False) -> bool:
    db_path = _db_path(net_cfg)
    db_exists = db_path.exists() and db_path.stat().st_size > 0
    if db_exists and not force:
        return False

    endpoint = bootstrap_url or getattr(net_cfg, "bootstrap_url", None)
    if not endpoint:
        return False

    if not quiet:
        typer.echo(f"Auto-bootstrap: fetching seeds from {endpoint}")

    try:
        _fetch_bootstrap_data(net_cfg, endpoint)
        if not quiet:
            typer.secho("✓ Bootstrap metadata saved locally", fg=typer.colors.GREEN)
        return True
    except Exception as exc:
        if not quiet:
            typer.secho(f"Warning: auto-bootstrap failed ({exc})", fg=typer.colors.YELLOW, err=True)
        return False


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
            f"Node lifecycle management for {network} requires the compose setup.",
            err=True
        )
        typer.echo(
            f"\nExpected location: {compose_file}",
            err=True
        )
        raise typer.Exit(code=1)
    
    return compose_file


@app.command()
def status(
    rpc_url: Optional[str] = typer.Option(
        None, "--rpc-url", help="JSON-RPC endpoint", envvar=RPC_ENV
    ),
    retry_delay: float = typer.Option(
        1.0, "--retry-delay", help="Delay between RPC retry attempts in seconds (default: 1.0)", envvar="ANIMICA_RETRY_DELAY"
    ),
    max_retries: int = typer.Option(
        3, "--max-retries", help="Maximum number of RPC attempts before failing fast"
    ),
    timeout: Optional[float] = typer.Option(
        None,
        "--timeout",
        help=f"JSON-RPC request timeout in seconds (default: {describe_timeout(DEFAULT_RPC_TIMEOUT)})",
        envvar=RPC_TIMEOUT_ENV,
    )
) -> None:
    """Show chain head, block info and sync state. Retries with bounded attempts on RPC errors."""
    url = _resolve_rpc_url(rpc_url)
    try:
        rpc_timeout = resolve_timeout("RPC timeout", timeout, env_var=RPC_TIMEOUT_ENV)
    except ValueError as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(code=1)
    
    # Validate retry delay
    if retry_delay <= 0:
        typer.echo(f"Error: retry-delay must be greater than 0, got {retry_delay}", err=True)
        raise typer.Exit(code=1)
    if max_retries < 1:
        typer.echo("Error: max-retries must be at least 1", err=True)
        raise typer.Exit(code=1)
    
    # Bounded retry loop for RPC operations
    attempt = 0
    backoff_delay = retry_delay
    while attempt < max_retries:
        attempt += 1
        try:
            head = asyncio.run(rpc_call("chain.getHead", [], rpc_url=url, timeout=rpc_timeout))
            height = head.get("height") or head.get("number") or 0
            chain_id = head.get("chainId") or head.get("chain_id")
            head_hash = head.get("hash") or head.get("blockHash")
            
            block = None
            if height is not None:
                try:
                    block = asyncio.run(
                        rpc_call("chain.getBlockByHeight", [height], rpc_url=url, timeout=rpc_timeout)
                    )
                except Exception:
                    block = None

            sync_status = None
            for method in ("node.syncStatus", "chain.syncing", "sync.isSyncing"):
                try:
                    sync_status = asyncio.run(rpc_call(method, [], rpc_url=url, timeout=rpc_timeout))
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
            
            # Success - exit the retry loop
            return
            
        except Exception as e:
            # RPC error - retry with backoff
            import time
            from datetime import datetime
            timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            error_message = str(e).strip()
            if not error_message:
                error_message = repr(e)
            if attempt >= max_retries:
                typer.echo(
                    f"[{timestamp}] Node status failed after {attempt} attempts: {error_message}",
                    err=True,
                )
                cached = _load_sync_state(load_network_config())
                if cached:
                    typer.secho(
                        "\n⚠ RPC unavailable. Showing last persisted sync state from 'animica sync force'.",
                        fg=typer.colors.YELLOW,
                    )
                    typer.echo(f"RPC URL: {cached.get('rpc_url', 'unknown')} (cached)")
                    typer.echo(f"Chain ID: {cached.get('chain_id')}")
                    typer.echo(f"Head height: {cached.get('height')}")
                    typer.echo(f"Head hash: {cached.get('head_hash')}")
                    typer.echo(f"Peer count: {cached.get('peer_count')}")
                    updated_at = _format_sync_timestamp(cached.get("updated_at"))
                    if updated_at:
                        typer.echo(f"Updated at: {updated_at}")
                    raise typer.Exit(code=0)
                raise typer.Exit(code=1)

            typer.echo(
                f"[{timestamp}] Retrying node status (attempt {attempt} failed: {error_message}). Retrying in {backoff_delay:.1f}s...",
                err=True,
            )
            time.sleep(backoff_delay)
            backoff_delay *= 2
            continue  # Retry bounded number of times


@app.command()
def head(
    rpc_url: Optional[str] = typer.Option(
        None, "--rpc-url", help="JSON-RPC endpoint", envvar=RPC_ENV
    ),
    timeout: Optional[float] = typer.Option(
        None,
        "--timeout",
        help=f"JSON-RPC request timeout in seconds (default: {describe_timeout(DEFAULT_RPC_TIMEOUT)})",
        envvar=RPC_TIMEOUT_ENV,
    ),
) -> None:
    """Print the current chain head summary."""
    url = _resolve_rpc_url(rpc_url)
    try:
        rpc_timeout = resolve_timeout("RPC timeout", timeout, env_var=RPC_TIMEOUT_ENV)
    except ValueError as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(code=1)
    head_info = asyncio.run(rpc_call("chain.getHead", [], rpc_url=url, timeout=rpc_timeout))
    typer.echo(_pretty(head_info))


@app.command()
def bootstrap(
    network: Optional[str] = typer.Option(
        None, "--network", help="Network to bootstrap (defaults to current network)", envvar="ANIMICA_NETWORK"
    ),
    bootstrap_url: Optional[str] = typer.Option(
        None,
        "--bootstrap-url",
        help="Bootstrap RPC endpoint (defaults to network bootstrap URL)",
        envvar="ANIMICA_BOOTSTRAP_RPC_URL",
    ),
    detach: bool = typer.Option(True, "--detach/--no-detach", help="Run node in background while bootstrapping"),
    max_peer_wait: int = typer.Option(60, "--peer-wait", help="Seconds to wait for peers after start"),
    retry_seeds: int = typer.Option(1, "--retry-seeds", help="Retry seed fetches when no peers"),
) -> None:
    """Bootstrap a node using the public bootstrap RPC, then switch to local RPC."""

    net_cfg = load_network_config(network)
    bootstrap_endpoint = bootstrap_url or net_cfg.bootstrap_url
    if not bootstrap_endpoint:
        typer.echo("No bootstrap RPC configured for this network", err=True)
        raise typer.Exit(code=1)

    db_path = _db_path(net_cfg)
    db_exists = db_path.exists() and db_path.stat().st_size > 0

    typer.secho(f"Bootstrapping network: {net_cfg.name}", fg=typer.colors.CYAN, bold=True)
    typer.echo(f"Bootstrap RPC: {bootstrap_endpoint}")
    typer.echo(f"Local RPC: {net_cfg.rpc_url}")
    typer.echo(f"Data dir: {net_cfg.data_dir} (db exists: {'yes' if db_exists else 'no'})")

    try:
        manifest, seeds, state_path = _fetch_bootstrap_data(net_cfg, bootstrap_endpoint)
    except Exception as exc:
        typer.echo(f"Failed to fetch bootstrap manifest: {exc}", err=True)
        raise typer.Exit(code=1)

    typer.echo(f"Saved bootstrap state to {state_path}")

    # Ensure subsequent commands use this network
    os.environ["ANIMICA_NETWORK"] = net_cfg.name

    # Start node using existing up command
    try:
        up(detach=detach, build=True, with_miner=False)
    except SystemExit as exc:  # Typer exits bubble up as SystemExit
        if exc.code not in (0, None):
            raise
    except Exception as exc:
        typer.echo(f"Failed to start node: {exc}", err=True)
        raise typer.Exit(code=1)

    local_rpc = net_cfg.rpc_url
    typer.echo("Waiting for local node to become ready...")
    ready = False
    for _ in range(30):
        try:
            _local_rpc(local_rpc, "chain.getHead", [])
            ready = True
            break
        except Exception:
            time.sleep(2)
    if not ready:
        typer.echo("Node did not respond on local RPC within timeout", err=True)
        raise typer.Exit(code=1)

    attempts = max(1, max_peer_wait)
    refreshes = retry_seeds
    for attempt in range(attempts):
        try:
            peers = _local_rpc(local_rpc, "p2p.listPeers", [])
            peer_count = len(peers) if isinstance(peers, list) else int(peers or 0)
        except Exception:
            peer_count = 0

        if peer_count > 0:
            typer.secho(f"Peers connected: {peer_count}. Bootstrap complete.", fg=typer.colors.GREEN)
            break

        if peer_count == 0 and refreshes > 0 and attempt and attempt % 10 == 0:
            try:
                refreshed = _bootstrap_rpc(bootstrap_endpoint, "bootstrap.getSeeds")
                new_seeds = list(refreshed.get("seeds") or [])
                if new_seeds:
                    seeds = new_seeds
                refreshes -= 1
            except Exception:
                pass
            for seed in seeds:
                try:
                    _local_rpc(local_rpc, "p2p.addPeer", [str(seed)])
                except Exception:
                    continue

        time.sleep(1)
    else:
        typer.echo("Warning: no peers connected after bootstrap window", err=True)


@app.command()
def block(
    height: Optional[int] = typer.Option(None, "--height", help="Block height"),
    hash: Optional[str] = typer.Option(None, "--hash", help="Block hash"),
    rpc_url: Optional[str] = typer.Option(
        None, "--rpc-url", help="JSON-RPC endpoint", envvar=RPC_ENV
    ),
    timeout: Optional[float] = typer.Option(
        None,
        "--timeout",
        help=f"JSON-RPC request timeout in seconds (default: {describe_timeout(DEFAULT_RPC_TIMEOUT)})",
        envvar=RPC_TIMEOUT_ENV,
    ),
) -> None:
    """Fetch and display a block by height or hash."""
    if not height and not hash:
        raise typer.BadParameter("Provide --height or --hash")
    url = _resolve_rpc_url(rpc_url)
    try:
        rpc_timeout = resolve_timeout("RPC timeout", timeout, env_var=RPC_TIMEOUT_ENV)
    except ValueError as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(code=1)
    if height is not None:
        result = asyncio.run(rpc_call("chain.getBlockByHeight", [height], rpc_url=url, timeout=rpc_timeout))
        if (
            isinstance(result, dict)
            and "transactions" not in result
            and result.get("hash")
        ):
            result = asyncio.run(
                rpc_call("chain.getBlockByHash", [result["hash"]], rpc_url=url, timeout=rpc_timeout)
            )
    else:
        result = asyncio.run(rpc_call("chain.getBlockByHash", [hash], rpc_url=url, timeout=rpc_timeout))
    typer.echo(_pretty(result))


@app.command()
def tx(
    hash: str = typer.Option(..., "--hash", help="Transaction hash"),
    rpc_url: Optional[str] = typer.Option(
        None, "--rpc-url", help="JSON-RPC endpoint", envvar=RPC_ENV
    ),
    timeout: Optional[float] = typer.Option(
        None,
        "--timeout",
        help=f"JSON-RPC request timeout in seconds (default: {describe_timeout(DEFAULT_RPC_TIMEOUT)})",
        envvar=RPC_TIMEOUT_ENV,
    ),
) -> None:
    """Fetch and display a transaction by hash."""
    url = _resolve_rpc_url(rpc_url)
    try:
        rpc_timeout = resolve_timeout("RPC timeout", timeout, env_var=RPC_TIMEOUT_ENV)
    except ValueError as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(code=1)
    result = asyncio.run(rpc_call("chain.getTransactionByHash", [hash], rpc_url=url, timeout=rpc_timeout))
    typer.echo(_pretty(result))


@app.command()
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
    with_miner: bool = typer.Option(
        False,
        "--with-miner",
        help="Also start miner service (uses 'miner' profile)"
    ),
) -> None:
    """
    Start an Animica node using Docker Compose.
    
    This command spins up a node with the configured network settings.
    The compose file and configuration are automatically selected based on
    the active network (set via 'animica network set <network>').
    
    Network-specific default host ports:
      - mainnet: RPC 8545, P2P 30333, Metrics 9000
      - testnet: RPC 18546, P2P 31334, Metrics 19000
      - devnet: RPC 28545, P2P 31335, Metrics 29000
      - local-devnet: RPC 38545, P2P 31336, Metrics 39000
    
    Each network uses isolated data directories and volumes to prevent cross-network
    contamination of blockchain data. Ports can be customized via environment variables:
      HOST_RPC_PORT, HOST_P2P_PORT, HOST_METRICS_PORT
    
    Note: Studio Services (deploy/verify API) are NOT started by default.
    To start Studio Services, use 'animica studio up' after the node is running.
    
    Before running this command, ensure you have set a network using:
      animica network set <network>
    
    Examples:
      animica network set mainnet
      animica node up
      
      animica network set testnet
      animica node up --no-detach  # Run in foreground
      
      animica network set devnet
      animica node up --with-miner  # Start node with miner
      
      # Override default ports
      HOST_RPC_PORT=9545 animica node up
      
    To also start Studio Services (optional):
      animica node up
      animica studio up  # Start studio services separately
    """
    # Enforce network requirement
    network = _ensure_network_set()
    
    # Get network-specific compose file
    compose_file = _get_compose_file(network)
    net_cfg = load_network_config(network)
    data_dir = str(Path(net_cfg.data_dir).expanduser())
    
    defaults = get_network_defaults(network)
    net_cfg = load_network_config(network)
    data_dir = str(Path(net_cfg.data_dir).expanduser())

    _auto_bootstrap_if_needed(net_cfg, os.getenv("ANIMICA_BOOTSTRAP_RPC_URL"), quiet=False)
    
    typer.secho(f"Starting node for network: {network}", fg=typer.colors.CYAN, bold=True)
    typer.echo(f"Using compose file: {compose_file}")
    typer.echo(f"Chain ID: {defaults['chain_id']}")
    typer.echo(f"Host RPC Port: {os.environ.get('HOST_RPC_PORT', defaults['rpc_port'])}")
    typer.echo(f"Host P2P Port: {os.environ.get('HOST_P2P_PORT', defaults['p2p_port'])}")
    typer.echo(f"Host Metrics Port: {os.environ.get('HOST_METRICS_PORT', defaults['metrics_port'])}")
    typer.echo(f"Data directory: {data_dir}")
    
    # Build docker-compose command
    # For devnet, we need to use profiles; for mainnet/testnet, services run by default
    cmd = [
        "docker", "compose",
        "-f", str(compose_file),
    ]
    
    # Add profiles based on network and options
    if network in DEV_NETWORKS:
        # Devnet uses profiles: 'dev' for node+miner by default
        cmd.extend(["--profile", "dev"])
    
    if with_miner and network not in DEV_NETWORKS:
        # For mainnet/testnet, miner is in separate profile
        cmd.extend(["--profile", "miner"])
    
    if build:
        cmd.extend(["up", "--build"])
    else:
        cmd.append("up")
    
    if detach:
        cmd.append("-d")
    
    typer.echo(f"\nRunning: {' '.join(cmd)}")
    typer.echo("This may take a few minutes on first run...\n")
    
    compose_env = {
        **os.environ,
        "ANIMICA_NETWORK": network,
        "ANIMICA_DATA_DIR": data_dir,
        "ANIMICA_P2P_DATA_DIR": str(Path(data_dir) / "p2p"),
    }

    try:
        result = subprocess.run(
            cmd,
            cwd=compose_file.parent,
            check=False,
            env=compose_env,
        )
        
        if result.returncode == 0:
            typer.secho("✓ Node started successfully!", fg=typer.colors.GREEN, bold=True)
            if detach:
                typer.echo(f"\nNode is running in the background on network: {network}")
                typer.echo(f"View logs with: docker compose -f {compose_file} logs -f")
                typer.echo("Check status with: animica node status")
                if network == "mainnet":
                    typer.echo("\n--- Mainnet Node Running ---")
                    typer.echo("To check balances:")
                    typer.echo("  animica wallet show <address|label>")
                    typer.echo("  animica rpc call state.getBalance '{\"params\": [\"<address>\"]}'")
                elif network == "testnet":
                    typer.echo("\n--- Testnet Node Running ---")
                    typer.echo("Request testnet tokens from faucet if available")
                else:
                    typer.echo("\n--- Devnet Node Running ---")
                    typer.echo("Premine accounts available for testing")
                typer.echo("\nWallet file location: ~/.animica/wallets.json")
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


@app.command(name="up-all")
def up_all(
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
    with_miner: bool = typer.Option(
        False,
        "--with-miner",
        help="Also start miner service for applicable networks"
    ),
) -> None:
    """
    Start all Animica node networks at once.
    
    This command sequentially starts all supported networks with non-conflicting
    default host ports:
    - mainnet (chain ID 1): RPC 8545, P2P 30333, Metrics 9000
    - testnet (chain ID 2): RPC 18546, P2P 31334, Metrics 19000
    - devnet (chain ID 1337): RPC 28545, P2P 31335, Metrics 29000
    - local-devnet (chain ID 1337): RPC 38545, P2P 31336, Metrics 39000
    
    Each network uses its own compose file and data directory to prevent
    cross-network contamination. The command will attempt to start all
    networks and report progress for each one.
    
    If a network's compose file is missing, it will be skipped with a warning.
    If any network fails to start, the command will continue with remaining
    networks but will exit with a non-zero code at the end.
    
    Port customization: Set environment variables before running:
      HOST_RPC_PORT, HOST_P2P_PORT, HOST_METRICS_PORT (apply globally to all networks)
    
    Examples:
      animica node up-all
      animica node up-all --no-detach  # Run in foreground
      animica node up-all --with-miner # Start all networks with miners
    """
    # List of all supported networks
    all_networks = ["mainnet", "testnet", "devnet", "local-devnet"]
    
    typer.secho("Starting all Animica node networks...", fg=typer.colors.CYAN, bold=True)
    typer.echo(f"Networks to start: {', '.join(all_networks)}\n")
    
    failed_networks = []
    skipped_networks = []
    successful_networks = []
    
    for network in all_networks:
        typer.secho(f"\n{'='*60}", fg=typer.colors.CYAN)
        typer.secho(f"Starting network: {network}", fg=typer.colors.CYAN, bold=True)
        typer.secho(f"{'='*60}", fg=typer.colors.CYAN)
        
        # Get network-specific compose file
        try:
            defaults = get_network_defaults(network)
            compose_file = defaults["compose_file"]
            net_cfg = load_network_config(network)
            data_dir = str(Path(net_cfg.data_dir).expanduser())
            
            if not compose_file.exists():
                typer.secho(
                    f"⚠ Warning: Compose file not found for {network}: {compose_file}",
                    fg=typer.colors.YELLOW
                )
                typer.echo(f"Skipping {network}...\n")
                skipped_networks.append(network)
                continue
        except Exception as e:
            typer.secho(
                f"⚠ Warning: Error getting compose file for {network}: {e}",
                fg=typer.colors.YELLOW
            )
            typer.echo(f"Skipping {network}...\n")
            skipped_networks.append(network)
            continue
        
        _auto_bootstrap_if_needed(net_cfg, os.getenv("ANIMICA_BOOTSTRAP_RPC_URL"), quiet=True)

        typer.echo(f"Compose file: {compose_file}")
        typer.echo(f"Chain ID: {defaults['chain_id']}")
        typer.echo(f"Host RPC Port: {os.environ.get('HOST_RPC_PORT', defaults['rpc_port'])}")
        typer.echo(f"Host P2P Port: {os.environ.get('HOST_P2P_PORT', defaults['p2p_port'])}")
        typer.echo(f"Host Metrics Port: {os.environ.get('HOST_METRICS_PORT', defaults['metrics_port'])}")
        typer.echo(f"Data directory: {data_dir}")
        
        # Build docker-compose command
        cmd = [
            "docker", "compose",
            "-f", str(compose_file),
        ]
        
        # Add profiles based on network and options
        if network in DEV_NETWORKS:
            cmd.extend(["--profile", "dev"])
        
        if with_miner and network not in DEV_NETWORKS:
            cmd.extend(["--profile", "miner"])
        
        if build:
            cmd.extend(["up", "--build"])
        else:
            cmd.append("up")
        
        if detach:
            cmd.append("-d")

        typer.echo(f"\nRunning: {' '.join(cmd)}")

        compose_env = {
            **os.environ,
            "ANIMICA_NETWORK": network,
            "ANIMICA_DATA_DIR": data_dir,
            "ANIMICA_P2P_DATA_DIR": str(Path(data_dir) / "p2p"),
        }

        try:
            result = subprocess.run(
                cmd,
                cwd=compose_file.parent,
                check=False,
                env=compose_env,
                capture_output=True,
                text=True
            )
            
            if result.returncode == 0:
                typer.secho(f"✓ {network} started successfully!", fg=typer.colors.GREEN, bold=True)
                successful_networks.append(network)
            else:
                typer.secho(
                    f"✗ {network} failed to start (exit code {result.returncode})",
                    fg=typer.colors.RED,
                    bold=True
                )
                if result.stderr:
                    typer.echo(f"Error output:\n{result.stderr}", err=True)
                failed_networks.append(network)
                
        except FileNotFoundError:
            typer.secho(
                f"✗ Error: 'docker' command not found.",
                fg=typer.colors.RED
            )
            typer.echo("Please install Docker and Docker Compose.", err=True)
            failed_networks.append(network)
            break  # No point continuing if docker is not installed
        except Exception as e:
            typer.secho(
                f"✗ {network} failed with unexpected error: {e}",
                fg=typer.colors.RED
            )
            failed_networks.append(network)
    
    # Print summary
    typer.secho(f"\n{'='*60}", fg=typer.colors.CYAN)
    typer.secho("Summary", fg=typer.colors.CYAN, bold=True)
    typer.secho(f"{'='*60}", fg=typer.colors.CYAN)
    
    if successful_networks:
        typer.secho(
            f"✓ Successfully started ({len(successful_networks)}): {', '.join(successful_networks)}",
            fg=typer.colors.GREEN
        )
    
    if skipped_networks:
        typer.secho(
            f"⚠ Skipped ({len(skipped_networks)}): {', '.join(skipped_networks)}",
            fg=typer.colors.YELLOW
        )
    
    if failed_networks:
        typer.secho(
            f"✗ Failed ({len(failed_networks)}): {', '.join(failed_networks)}",
            fg=typer.colors.RED,
            bold=True
        )
        typer.echo(
            "\nSome networks failed to start. Check the error messages above for details.",
            err=True
        )
        raise typer.Exit(code=1)
    
    if not successful_networks and not failed_networks:
        typer.secho(
            "⚠ No networks were started. All were skipped.",
            fg=typer.colors.YELLOW
        )
        raise typer.Exit(code=1)
    
    typer.secho("\n✓ All requested networks started successfully!", fg=typer.colors.GREEN, bold=True)


@app.command()
def down(
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
    
    The command automatically uses the correct compose file based on the
    active network setting.
    
    Before running this command, ensure you have set a network using:
      animica network set <network>
    
    Examples:
      animica node down
      animica node down --volumes  # Also delete blockchain data
    
    WARNING: Using --volumes will delete all blockchain data for the active network!
    """
    # Enforce network requirement
    network = _ensure_network_set()
    
    # Get network-specific compose file
    compose_file = _get_compose_file(network)
    net_cfg = load_network_config(network)
    data_dir = str(Path(net_cfg.data_dir).expanduser())
    
    typer.secho(f"Stopping node for network: {network}", fg=typer.colors.CYAN, bold=True)
    typer.echo(f"Using compose file: {compose_file}")
    
    if volumes:
        typer.secho(
            f"\n⚠ WARNING: --volumes flag will delete all {network} blockchain data!",
            fg=typer.colors.YELLOW,
            bold=True
        )
    
    # Build docker-compose command
    cmd = [
        "docker", "compose",
        "-f", str(compose_file),
    ]
    
    # Add profiles for devnet
    if network in DEV_NETWORKS:
        cmd.extend(["--profile", "dev"])
    
    cmd.append("down")
    
    if volumes:
        cmd.append("-v")
    
    typer.echo(f"\nRunning: {' '.join(cmd)}\n")

    compose_env = {
        **os.environ,
        "ANIMICA_NETWORK": network,
        "ANIMICA_DATA_DIR": data_dir,
        "ANIMICA_P2P_DATA_DIR": str(Path(data_dir) / "p2p"),
    }

    try:
        result = subprocess.run(
            cmd,
            cwd=compose_file.parent,
            check=False,
            env=compose_env,
        )
        
        if result.returncode == 0:
            typer.secho("✓ Node stopped successfully!", fg=typer.colors.GREEN, bold=True)
            if volumes:
                typer.echo(f"All volumes and {network} blockchain data have been removed.")
            else:
                typer.echo(f"{network.capitalize()} blockchain data has been preserved in volumes.")
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
