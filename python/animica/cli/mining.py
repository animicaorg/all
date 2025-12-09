"""Mining operations CLI for Animica.

Provides commands for:
  - Mining blocks via RPC (mine-blocks)
  - Running the Stratum pool server (run-pool)
  - Inspecting pool configuration (show-config)
  - Generating payout addresses (generate-payout-address)
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path
from typing import Optional

import typer
from animica.config import load_network_config

try:
    from animica.cli.wallet import (WalletEntry, _wallet_file_path,
                                    create_wallet)
    from animica.stratum_pool import cli as pool_cli
    from animica.stratum_pool.config import PoolConfig, load_config_from_env

    HAVE_STRATUM = True
except Exception:
    HAVE_STRATUM = False

app = typer.Typer(help="Mining operations and Stratum pool management.")

RPC_ENV = "ANIMICA_RPC_URL"
DB_ENV = "ANIMICA_MINING_POOL_DB_URL"
LOG_LEVEL_ENV = "ANIMICA_MINING_POOL_LOG_LEVEL"
STRATUM_BIND_ENV = "ANIMICA_STRATUM_BIND"
API_BIND_ENV = "ANIMICA_POOL_API_BIND"


def _ensure_network_env() -> None:
    cfg = load_network_config()
    os.environ.setdefault("ANIMICA_NETWORK", cfg.name)
    os.environ.setdefault(RPC_ENV, cfg.rpc_url)


def _ensure_stratum_available() -> None:
    if not HAVE_STRATUM:
        typer.echo(
            "Error: Stratum pool modules required. "
            "Ensure 'animica[stratum]' is installed.",
            err=True,
        )
        raise typer.Exit(1)


@app.command("run-pool")
def run_pool(
    rpc_url: Optional[str] = typer.Option(
        None, "--rpc-url", help="Animica node RPC URL", envvar=RPC_ENV
    ),
    db_url: Optional[str] = typer.Option(
        None, "--db-url", help="Database URL", envvar=DB_ENV
    ),
    stratum_bind: Optional[str] = typer.Option(
        None, "--stratum-bind", help="Stratum bind address", envvar=STRATUM_BIND_ENV
    ),
    api_bind: Optional[str] = typer.Option(
        None, "--api-bind", help="API bind address", envvar=API_BIND_ENV
    ),
    log_level: Optional[str] = typer.Option(
        None, "--log-level", help="Log level", envvar=LOG_LEVEL_ENV
    ),
) -> None:
    """Start the Animica Stratum mining pool."""
    _ensure_stratum_available()
    _ensure_network_env()
    env_overrides = {
        RPC_ENV: rpc_url,
        DB_ENV: db_url,
        STRATUM_BIND_ENV: stratum_bind,
        API_BIND_ENV: api_bind,
        LOG_LEVEL_ENV: log_level,
    }
    for key, value in env_overrides.items():
        if value is not None:
            os.environ[key] = value
    pool_cli.main([])


@app.command("show-config")
def show_config() -> None:
    """Display the effective pool configuration."""
    _ensure_stratum_available()
    _ensure_network_env()
    # load_config_from_env is provided by animica.stratum_pool when installed
    try:
        cfg: PoolConfig = load_config_from_env()
    except Exception as e:
        typer.echo(
            "Error: could not load pool config; ensure animica[stratum] is installed",
            err=True,
        )
        raise typer.Exit(1)
    typer.echo(
        f"RPC URL: {cfg.rpc_url}\n"
        f"DB URL: {cfg.db_url}\n"
        f"Chain ID: {cfg.chain_id}\n"
        f"Pool address: {cfg.pool_address}\n"
        f"Stratum bind: {cfg.host}:{cfg.port}\n"
        f"API bind: {cfg.api_host}:{cfg.api_port}\n"
        f"Log level: {cfg.log_level}"
    )


@app.command("generate-payout-address")
def generate_payout_address(
    wallet_file: Optional[Path] = typer.Option(
        None, "--wallet-file", help="Wallet store for generated address"
    ),
    label: str = typer.Option(
        "pool-payout", "--label", help="Label for the generated wallet"
    ),
) -> None:
    """Generate a dev wallet for pool payouts using the wallet CLI helpers."""
    # Delegate to wallet module for key generation (no stratum dependency required)
    try:
        from animica.cli.wallet import (_generate_entry, _load_store,
                                        _save_store, _wallet_file_path)

        path = _wallet_file_path(wallet_file)
        store = _load_store(path)
        entry = _generate_entry(label, allow_fallback=True)
        store.setdefault("wallets", []).append(entry.to_dict())
        _save_store(path, store)
        typer.echo(f"Generated payout address {entry.address} (label: {entry.label})")
    except Exception as e:
        typer.echo(f"Error generating payout address: {e}", err=True)
        raise typer.Exit(1)


@app.command("mine-blocks")
def mine_blocks(
    address: str = typer.Option(
        ...,
        "--address",
        help="Payout address for mined blocks (e.g., anim1...)",
    ),
    count: int = typer.Option(
        ...,
        "--count",
        help="Number of blocks to mine (must be > 0)",
    ),
    rpc_url: Optional[str] = typer.Option(
        None,
        "--rpc-url",
        help="Node JSON-RPC endpoint URL",
        envvar="ANIMICA_RPC_URL",
    ),
) -> None:
    """
    Mine a specified number of blocks to a given address.
    
    This command uses the node's mining RPC to mine blocks for testing
    and development purposes.
    
    Examples:
        animica miner mine-blocks --address anim1test123 --count 5
        animica miner mine-blocks --address anim1test123 --count 10 --rpc-url http://localhost:8545
    
    Note: The current miner.mine RPC method does not support payout address selection.
    Blocks will be mined to the node's default miner address. The --address parameter
    is accepted for future compatibility.
    """
    # Convert count to int if needed (stub Typer may pass as string)
    if isinstance(count, str):
        try:
            count = int(count)
        except ValueError:
            typer.secho(
                f"Error: count must be a valid integer, got {count}",
                fg=typer.colors.RED,
                err=True,
            )
            raise typer.Exit(2)
    
    # Validate count
    if count <= 0:
        typer.secho(
            f"Error: count must be greater than 0, got {count}",
            fg=typer.colors.RED,
            err=True,
        )
        raise typer.Exit(2)
    
    # Validate address
    if not address or not address.strip():
        typer.secho(
            "Error: address is required",
            fg=typer.colors.RED,
            err=True,
        )
        raise typer.Exit(2)
    
    # Resolve RPC URL
    url = rpc_url or os.environ.get("ANIMICA_RPC_URL") or load_network_config().rpc_url
    
    # Try to import RPC client
    rpc_client = None
    try:
        from sdk.python.omni_sdk.rpc.http import RpcClient
        rpc_client = RpcClient
    except (ImportError, ModuleNotFoundError, RuntimeError):
        try:
            from omni_sdk.rpc.http import RpcClient  # type: ignore
            rpc_client = RpcClient
        except (ImportError, ModuleNotFoundError, RuntimeError):
            pass
    
    if rpc_client is None:
        typer.secho(
            "Error: RpcClient not available. Please install omni_sdk: pip install -e sdk/python",
            fg=typer.colors.RED,
            err=True,
        )
        raise typer.Exit(3)
    
    typer.echo(
        f"Mining {count} block(s) with payout to address {address} via RPC {url}"
    )
    typer.secho(
        "Note: The current miner.mine RPC method does not support payout address selection. "
        "Blocks will be mined to the node's default miner address. "
        "The --address parameter is accepted for future compatibility.",
        fg=typer.colors.YELLOW,
    )
    
    try:
        with rpc_client(url, timeout=30.0) as client:
            result = client.request("miner.mine", [count])
            
            mined = result.get("mined", 0)
            height = result.get("height", 0)
            
            if mined == 0:
                typer.secho(
                    "Warning: No blocks were mined (may have failed)",
                    fg=typer.colors.YELLOW,
                )
                raise typer.Exit(4)
            elif mined < count:
                typer.secho(
                    f"Warning: Only {mined} of {count} requested blocks were mined",
                    fg=typer.colors.YELLOW,
                )
            
            typer.secho(
                f"✓ Successfully mined {mined} block(s). New chain height: {height}",
                fg=typer.colors.GREEN,
                bold=True,
            )
    
    except (RuntimeError, ConnectionError, OSError, TimeoutError) as e:
        typer.secho(
            f"Error: Failed to connect to RPC: {e}",
            fg=typer.colors.RED,
            err=True,
        )
        raise typer.Exit(5)
    except Exception as e:
        typer.secho(
            f"Error: Failed to mine blocks via RPC: {e}",
            fg=typer.colors.RED,
            err=True,
        )
        raise typer.Exit(5)


if __name__ == "__main__":  # pragma: no cover
    app()
